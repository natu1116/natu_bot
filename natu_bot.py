from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
from aiohttp import web
import json
import http.client
import sys

# 環境変数を読み込む
load_dotenv() 

# --- 共通設定 ---
# 権限を持つロールID (リアクションを付けることができるユーザーのロール)
AUTH_ROLE_ID = 1432204508536111155 
# 付与するロールID (コメントをしたユーザーに付与されるロール)
GRANT_ROLE_ID = 1432204383529078935
# 監視するリアクション絵文字
TARGET_EMOJI = '✅'
# チャットを起動する接頭辞
CHAT_TRIGGER_PREFIX = 'ボット、'

# プレゼンス通知を送信するチャンネルID
NOTIFICATION_CHANNEL_ID = 1445953441141882973 

# 💡 キーワード応答設定 
KEYWORD_RESPONSES = {
    "ありがとう": "どういたしまして！お役に立てて嬉しいです。",
    "さよなら": "またね！良い一日を！"
}

# --- Gemini API設定 ---
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = "gemini-2.5-flash-preview-09-2025"
GEMINI_API_HOST = "generativelanguage.googleapis.com"
# APIキーが設定されていない場合は、エラーを避けるためにパスを空にする
GEMINI_API_PATH = f"/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}" if GEMINI_API_KEY else ""


# --- Discord Botの設定 ---
# 必要なインテントをすべて有効化
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 
intents.presences = True # プレゼンス通知に必須
bot = commands.Bot(command_prefix='!', intents=intents)

# --- Gemini APIを利用したチャット処理 ---
async def generate_gemini_response(prompt: str) -> str:
    """Gemini APIを呼び出して応答を生成します"""
    
    if not GEMINI_API_KEY or not GEMINI_API_PATH:
        return "🚨 エラー: Gemini APIキーが設定されていません。チャット機能は無効です。"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {
            "parts": [{"text": "あなたはDiscordサーバーでフレンドリーに振る舞う、日本のチャットボットです。ユーザーの質問に親しみを込めて、日本語で答えてください。"}]
        }
    }
    
    headers = {'Content-Type': 'application/json'}
    
    # 同期処理であるHTTPリクエストを非同期で実行するための関数
    def make_request():
        try:
            conn = http.client.HTTPSConnection(GEMINI_API_HOST)
            conn.request("POST", GEMINI_API_PATH, json.dumps(payload), headers)
            response = conn.getresponse()
            
            if response.status != 200:
                error_body = response.read().decode()
                print(f"Gemini API Error: Status {response.status}, Body: {error_body}", file=sys.stderr)
                return f"🚨 APIエラーが発生しました: ステータス {response.status}"

            response_data = json.loads(response.read().decode())
            
            # 応答からテキストを抽出
            text = response_data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '🤔 応答を取得できませんでした。')
            return text
            
        except Exception as e:
            print(f"HTTP Request Error: {e}", file=sys.stderr)
            return f"🚨 通信エラーが発生しました: {e}"
        finally:
            if 'conn' in locals():
                conn.close()

    # asyncio.to_threadを使って、Botのイベントループをブロックせずに実行
    return await asyncio.to_thread(make_request)


# --- Webサーバー機能（ポートチェック回避用） ---

async def handle_health_check(request):
    """ホスティングサービスのヘルスチェックに応答するためのハンドラ。"""
    if bot.is_ready():
        return web.Response(text="OK", content_type='text/plain', status=200)
    else:
        return web.Response(text="Bot is initializing...", content_type='text/plain', status=503)

async def web_server_start():
    """Webサーバーを非同期で起動します。"""
    # Renderの環境変数 'PORT' からポート番号を取得。
    port = int(os.environ.get("PORT", 8080))
    
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    
    try:
        await site.start()
        print(f"✅ Web server started successfully on port {port} (for health check).")
    except Exception as e:
        print(f"🚨 FATAL ERROR: Webサーバーの起動に失敗しました。このエラーが出るとRenderはBotを停止します。{e}", file=sys.stderr)


# --- プレゼンス更新イベント（オンライン/オフライン通知） ---
@bot.event
async def on_presence_update(before, after):
    """メンバーのステータスが変更されたときに通知を送信します"""
    
    if after.id == bot.user.id:
        return
        
    # ステータスが変更されていない場合は無視 (例: アクティビティのみの変更)
    if before.status == after.status:
        return
        
    notification_channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
    if notification_channel is None:
        # printをsys.stderrに出力して、ホスティング環境でのログを明確にする
        print(f"🚨 通知チャンネルID {NOTIFICATION_CHANNEL_ID} が見つかりません。", file=sys.stderr)
        return

    display_name = after.display_name 

    if after.status == discord.Status.online and before.status != discord.Status.online:
        # オフラインやDND、アイドルからオンラインへ
        message = f"**{display_name}** がオンラインになりました！ 👋"
        
    elif after.status == discord.Status.offline and before.status != discord.Status.offline:
        # オンライン、DND、アイドルからオフラインへ
        message = f"**{display_name}** がオフラインになりました。またね！ 😴"
        
    else:
        # その他のステータス変更は無視
        return

    try:
        await notification_channel.send(message)
        print(f"🔔 プレゼンス通知を送信: {message}")
    except discord.Forbidden:
        print(f"🚨 通知チャンネル {NOTIFICATION_CHANNEL_ID} へのメッセージ送信権限がありません。", file=sys.stderr)
    except Exception as e:
        print(f"🚨 プレゼンス通知中にエラーが発生しました: {e}", file=sys.stderr)


# --- メッセージ受信イベント（キーワード応答とチャット処理） ---
@bot.event
async def on_message(message):
    """メッセージを受信したときに実行されるイベント"""
    
    if message.author == bot.user:
        return

    content_lower = message.content.lower()
    
    # 1. キーワード応答のチェック (最優先)
    for keyword, response in KEYWORD_RESPONSES.items():
        if keyword in content_lower:
            await message.reply(response)
            return # キーワード応答が完了したら終了

    # 2. チャットのトリガーを判定
    is_triggered = False
    prompt_text = ""
    
    # Botへのメンションの場合
    if bot.user.mentioned_in(message):
        prompt_text = message.content.replace(f'<@{bot.user.id}>', '').strip()
        is_triggered = True
    
    # 特定の接頭辞で始まる場合
    elif message.content.startswith(CHAT_TRIGGER_PREFIX):
        prompt_text = message.content[len(CHAT_TRIGGER_PREFIX):].strip()
        is_triggered = True

    if is_triggered and prompt_text:
        # Gemini APIによる応答
        async with message.channel.typing():
            response_text = await generate_gemini_response(prompt_text)
            
        await message.reply(response_text)
        
    # Botコマンドの処理を継続させる
    await bot.process_commands(message)


# --- ロール付与イベント（リアクション監視） ---
@bot.event
async def on_raw_reaction_add(payload):
    """リアクションが追加されたときに実行されます (ロール付与ロジック)"""

    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != TARGET_EMOJI:
        return
    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    reactor_member = guild.get_member(payload.user_id)
    if reactor_member is None:
        return

    # リアクターが権限ロールを持っているかを確認
    auth_role = discord.utils.get(guild.roles, id=AUTH_ROLE_ID)
    if auth_role is None or auth_role not in reactor_member.roles:
        return

    channel = bot.get_channel(payload.channel_id)
    if channel is None:
        return

    try:
        message = await channel.fetch_message(payload.message_id)
    except Exception:
        return

    target_user = message.author
    if target_user.bot or target_user is None:
        return

    grant_role = discord.utils.get(guild.roles, id=GRANT_ROLE_ID)
    if grant_role is None:
        return

    try:
        target_member = guild.get_member(target_user.id)
        
        if grant_role in target_member.roles:
            return
            
        await target_member.add_roles(grant_role, reason=f"リアクター {reactor_member.display_name} による {TARGET_EMOJI} リアクション")
        print(f"✅ ロール付与成功: {grant_role.name} を {target_member.display_name} に付与しました。")

    except discord.Forbidden:
        print(f"🚨 ロール付与失敗: Botに {grant_role.name} を付与する権限がありません。", file=sys.stderr)
    except Exception as e:
        print(f"🚨 予期せぬエラーが発生しました: {e}", file=sys.stderr)


# --- 起動処理 ---

@bot.event
async def on_ready():
    """BotがDiscordに接続し、準備が完了したときに実行されます。"""
    print('-------------------------------------')
    print(f'Botがログインしました: {bot.user}')
    print('-------------------------------------')
    # Botのイベントループ上でWebサーバーのタスクをスケジュール
    asyncio.create_task(web_server_start())


if __name__ == '__main__':
    BOT_TOKEN = os.getenv('TOKEN') 
    
    if not BOT_TOKEN:
        print("⚠️ エラー: 環境変数 'TOKEN' が設定されていません。'.env'ファイルを確認してください。", file=sys.stderr)
    else:
        try:
            bot.run(BOT_TOKEN)
        except Exception as e:
            print(f"致命的なエラーが発生しました: {e}", file=sys.stderr)
