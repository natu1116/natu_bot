import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
from aiohttp import web # 💡 aiohttpのWebモジュールをインポート

# 環境変数を読み込む (.envファイルから)
load_dotenv() 

# --- 設定 ---
AUTH_ROLE_ID = 1432204508536111155 
GRANT_ROLE_ID = 1432204383529078935
TARGET_EMOJI = '✅'

# --- Discord Botの設定 ---
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# --- 💡 Webサーバー機能（ポートチェック回避用） ---
async def handle_health_check(request):
    """
    Renderなどのホスティングサービスのヘルスチェックに応答するためのハンドラ。
    応答メッセージを最小限の固定テキストに保ちます (ログや出力サイズ超過エラー対策)。
    """
    # Botが準備完了状態（オンライン）か確認
    if bot.is_ready():
        # ステータス200 (OK) を返し、Botが稼働中であることを通知
        return web.Response(text="OK", content_type='text/plain', status=200)
    else:
        # 準備中でなければステータス503 (Service Unavailable) を返す
        return web.Response(text="Bot is initializing...", content_type='text/plain', status=503)

async def web_server():
    """
    Webサーバーを非同期で起動し、ヘルスチェックのエンドポイントを設定します。
    """
    # 環境変数 'PORT' からポート番号を取得。設定されていない場合は10000をデフォルトとして使用。
    port = int(os.environ.get("PORT", 10000))
    
    app = web.Application()
    
    # ルート ('/') に対してヘルスチェックハンドラを登録
    app.router.add_get("/", handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # 0.0.0.0で指定されたポートをリッスン
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    
    try:
        await site.start()
        print(f"Web server started on http://0.0.0.0:{port}/")
    except Exception as e:
        # 起動失敗は致命的なエラーとしてログに出力
        print(f"FATAL ERROR: Webサーバーの起動に失敗しました。{e}")

# --- Discord Bot イベントリスナー ---
@bot.event
async def on_ready():
    """BotがDiscordに接続したときに実行されます"""
    print('-------------------------------------')
    print(f'Botがログインしました: {bot.user}')
    # Webサーバーの起動ログは、web_server関数内ですでに出力されます
    print('-------------------------------------')

@bot.event
async def on_raw_reaction_add(payload):
    """リアクションが追加されたときに実行されます"""

    # 1. リアクションがBot自身のものではないかを確認
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

    # 4. リアクターが特定のロールを持っているかを確認
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
    BOT_TOKEN = os.getenv('TOKEN') 
    
    if not BOT_TOKEN:
        print("⚠️ エラー: 環境変数 'TOKEN' が設定されていません。'.env'ファイルを確認してください。")
    else:
        # WebサーバーとDiscord Botのタスクを並行して実行
        try:
            # Botのイベントループを取得
            loop = asyncio.get_event_loop()
            
            # 1. Webサーバータスクをスケジュール
            loop.create_task(web_server())
            
            # 2. Botを実行 (この run() はブロッキングメソッドで、ループが停止するまで実行されます)
            bot.run(BOT_TOKEN)
            
        except Exception as e:
            print(f"致命的なエラーが発生しました: {e}")
