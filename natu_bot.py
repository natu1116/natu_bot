import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
from aiohttp import web # Webサーバー構築のためにaiohttpをインポート

# 環境変数を読み込む (.envファイルからTOKENを取得)
load_dotenv() 

# --- 設定 ---
# 権限を持つロールID (リアクションを付けることができるユーザーのロール)
AUTH_ROLE_ID = 1432204508536111155 
# 付与するロールID (コメントをしたユーザーに付与されるロール)
GRANT_ROLE_ID = 1432204383529078935
# 監視するリアクション絵文字
TARGET_EMOJI = '✅'

# --- Discord Botの設定 ---
intents = discord.Intents.default()
# メンバー情報とメッセージ内容のインテントを有効化 (ロール付与に必須)
intents.members = True 
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Webサーバー機能（ポートチェック回避用） ---

async def handle_health_check(request):
    """
    ホスティングサービスのヘルスチェックに応答するためのハンドラ。
    Botの稼働状況を確認し、応答を返します。
    """
    if bot.is_ready():
        # Botが稼働中であれば200 OK
        return web.Response(text="OK", content_type='text/plain', status=200)
    else:
        # Botがまだ初期化中であれば503 Service Unavailable
        return web.Response(text="Bot is initializing...", content_type='text/plain', status=503)

async def web_server_start():
    """
    Webサーバーを非同期で起動します。Discord Botと同じイベントループで実行されます。
    """
    # 環境変数 'PORT' からポート番号を取得 (ホスティングサービスで必須)
    port = int(os.environ.get("PORT", 8080))
    
    app = web.Application()
    # ルート ('/') に対してヘルスチェックハンドラを登録
    app.router.add_get("/", handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # 0.0.0.0と指定されたポートでリッスンを開始
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    
    try:
        await site.start()
        print(f"✅ Web server started successfully on port {port} (for health check).")
    except Exception as e:
        # 起動失敗は致命的なエラー
        print(f"🚨 FATAL ERROR: Webサーバーの起動に失敗しました。{e}")


# --- Discord Bot イベントリスナー ---

@bot.event
async def on_ready():
    """
    BotがDiscordに接続し、準備が完了したときに実行されます。
    この非同期ループ上でWebサーバーのタスクをスケジュールします。
    """
    print('-------------------------------------')
    print(f'Botがログインしました: {bot.user}')
    print('-------------------------------------')
    
    # Botのイベントループ上でWebサーバーのタスクをスケジュール
    asyncio.create_task(web_server_start())


@bot.event
async def on_raw_reaction_add(payload):
    """リアクションが追加されたときに実行されます (ロール付与ロジック)"""

    # 1. Bot自身によるリアクションは無視
    if payload.user_id == bot.user.id:
        return

    # 2. ターゲットの絵文字かを確認
    if str(payload.emoji) != TARGET_EMOJI:
        return

    # 3. リアクションを付けたユーザー（リアクター）を取得
    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    reactor_member = guild.get_member(payload.user_id)
    if reactor_member is None:
        return

    # 4. リアクターが権限ロールを持っているかを確認
    auth_role = discord.utils.get(guild.roles, id=AUTH_ROLE_ID)
    if auth_role is None or auth_role not in reactor_member.roles:
        return

    # 5. リアクションが付いたメッセージを取得
    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except discord.NotFound:
        print(f"メッセージID: {payload.message_id} が見つかりませんでした。")
        return
    except Exception as e:
        print(f"メッセージ取得中にエラーが発生しました: {e}")
        return

    # 6. コメントをしたユーザー（ターゲット）を取得
    target_user = message.author
    
    # Botのコメントや不明なユーザーは無視
    if target_user.bot or target_user is None:
        return

    # 7. ターゲットユーザーに付与するロールを取得
    grant_role = discord.utils.get(guild.roles, id=GRANT_ROLE_ID)

    if grant_role is None:
        print(f"エラー: 付与ロールID {GRANT_ROLE_ID} が見つかりませんでした。")
        return

    # 8. ターゲットユーザーにロールを付与
    try:
        target_member = guild.get_member(target_user.id)
        
        # 既にロールを持っているかチェック
        if grant_role in target_member.roles:
            print(f"ロール {grant_role.name} は既に {target_member.display_name} に付与されています。")
            return
            
        await target_member.add_roles(grant_role, reason=f"リアクター {reactor_member.display_name} による {TARGET_EMOJI} リアクション")
        print(f"✅ ロール付与成功: {grant_role.name} を {target_member.display_name} に付与しました。")

    except discord.Forbidden:
        print(f"🚨 ロール付与失敗: Botに {grant_role.name} を付与する権限がありません。Botのロールが対象ロールより上に設定されているか確認してください。")
    except Exception as e:
        print(f"🚨 予期せぬエラーが発生しました: {e}")


# --- メイン実行ブロック ---
if __name__ == '__main__':
    # 環境変数 'TOKEN' からトークンを取得
    BOT_TOKEN = os.getenv('TOKEN') 
    
    if not BOT_TOKEN:
        print("⚠️ エラー: 環境変数 'TOKEN' が設定されていません。'.env'ファイルを確認してください。")
    else:
        try:
            # bot.run() を実行すると、その内部で非同期ループが起動し、on_readyイベントがトリガーされます。
            bot.run(BOT_TOKEN)
        except Exception as e:
            print(f"致命的なエラーが発生しました: {e}")
