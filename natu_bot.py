import os
import discord
from discord.ext import commands
import asyncio
from typing import Optional
import aiohttp
from aiohttp import web
import aiohttp_cors 
from datetime import datetime, timezone, timedelta

# Gemini APIクライアント
from google import genai
from google.genai.errors import APIError

# ---------------------------
# --- 環境設定 ---
# ---------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY_PRIMARY = os.environ.get("GEMINI_API_KEY") # Primary Key
GEMINI_API_KEY_SECONDARY = os.environ.get("GEMINI_API_KEY_SECONDARY") # Secondary Key
PORT = int(os.environ.get("PORT", 8080)) 

# 通知チャンネルIDの取得と変換
NOTIFICATION_CHANNEL_ID = os.environ.get("NOTIFICATION_CHANNEL_ID")
if NOTIFICATION_CHANNEL_ID:
    try:
        NOTIFICATION_CHANNEL_ID = int(NOTIFICATION_CHANNEL_ID)
    except ValueError:
        NOTIFICATION_CHANNEL_ID = None

# DMログの送信先ユーザーID
TARGET_USER_ID_FOR_LOGS = 1402481116723548330 

# ★ 追加: AIの接し方を定義するシステムプロンプト
# (日本語で設定し、Geminiに日本語での振る舞いを指示します)
AI_SYSTEM_PROMPT = (
    "あなたは、知識豊富で、フレンドリーかつ協力的、そして少しウィットに富んだアシスタントです。すべての質問に対して、"
    "簡潔で分かりやすい言葉で答えてください。専門的な用語を使う際は、必ず分かりやすい解説を加えてください。"
    "ユーザーの問いかけに対して、親しみやすいトーンで応じ、会話を楽しむように努めてください。"
    "なお、この会話は、ユーザーの問いかけに1度しか返す事ができないことを考えた返答をしてください。"
)


# Botの設定 (Intentsの設定が必要)
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# ----------------------------------------------------------------------
# Geminiクライアントの初期化とフォールバックリストの作成
# ----------------------------------------------------------------------
gemini_clients = []

def initialize_gemini_clients():
    """設定されたAPIキーに基づいてGeminiクライアントを初期化し、リストに格納します。"""
    global gemini_clients
    clients = []
    
    # Primary Keyの初期化
    if GEMINI_API_KEY_PRIMARY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY_PRIMARY)
            clients.append({'client': client, 'name': 'Primary'})
            print("Gemini Client (Primary) の初期化に成功しました。")
        except Exception as e:
            print(f"WARNING: Gemini Client (Primary) の初期化に失敗しました: {e}")

    # Secondary Keyの初期化
    if GEMINI_API_KEY_SECONDARY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY_SECONDARY)
            clients.append({'client': client, 'name': 'Secondary'})
            print("Gemini Client (Secondary) の初期化に成功しました。")
        except Exception as e:
            print(f"WARNING: Gemini Client (Secondary) の初期化に失敗しました: {e}")
            
    gemini_clients = clients
    return len(gemini_clients) > 0

initialize_gemini_clients() # Bot起動時にクライアントを初期化


# ----------------------------------------------------------------------
# DMログ送信ヘルパー関数
# ----------------------------------------------------------------------

async def send_dm_log(message: str, embed: Optional[discord.Embed] = None):
    """指定されたユーザーにDMとしてログを送信します。"""
    if TARGET_USER_ID_FOR_LOGS:
        try:
            # Botのキャッシュからユーザーを取得
            user = bot.get_user(TARGET_USER_ID_FOR_LOGS)
            if user is None:
                # キャッシュにない場合はフェッチを試みる
                user = await bot.fetch_user(TARGET_USER_ID_FOR_LOGS)

            if user:
                await user.send(content=message, embed=embed)
            else:
                print(f"ERROR: ユーザーID {TARGET_USER_ID_FOR_LOGS} が見つかりませんでした。DMログを送信できません。")
        except Exception as e:
            print(f"ERROR: DMログの送信中に予期せぬエラーが発生しました: {e}")


# ----------------------------------------------------------------------
# Discordイベントとスラッシュコマンド
# ----------------------------------------------------------------------

@bot.event
async def on_ready():
    """BotがDiscordに接続したときに実行されます。"""
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    JST = timezone(timedelta(hours=+9), 'JST')
    current_time_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S %Z")
    
    # 1. コマンドの同期
    try:
        synced = await bot.tree.sync()
        log_sync = f"DEBUG: {len(synced)}個のコマンドを同期しました。"
        print(log_sync)
    except Exception as e:
        log_sync = f"DEBUG: コマンドの同期中にエラーが発生しました: {e}"
        print(log_sync)
        
    # 2. ログイン通知のEmbed作成
    embed = discord.Embed(
        title="🤖 Botが正常に起動しました",
        description=f"環境変数 **PORT {PORT}** でWebサーバーが稼働中です。\n**有効なGeminiキー: {len(gemini_clients)}個**",
        color=discord.Color.green()
    )
    embed.add_field(name="接続ユーザー", value=f"{bot.user.name} (ID: {bot.user.id})", inline=False)
    embed.add_field(name="時刻 (JST)", value=current_time_jst, inline=False)

    # 3. ログイン通知の送信 (チャンネルとDMの両方)
    
    # a. 通知チャンネルへの送信
    if NOTIFICATION_CHANNEL_ID:
        try:
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)
                print(f"DEBUG: ログイン通知をチャンネル {NOTIFICATION_CHANNEL_ID} に送信しました。")
            else:
                print(f"DEBUG: ID {NOTIFICATION_CHANNEL_ID} のチャンネルが見つかりませんでした。")
        except Exception as e:
            print(f"DEBUG: ログイン通知の送信中にエラーが発生しました: {e}")

    # b. DMログ送信先への送信
    dm_message = f"**Bot起動ログ**\n時刻: {current_time_jst}\n有効キー数: {len(gemini_clients)}個\n{log_sync}"
    await send_dm_log(dm_message, embed=embed)
            
    print('------')


@bot.tree.command(name="ai", description="Gemini AIに質問を送信します。")
@discord.app_commands.describe(
    prompt="AIに話したい内容、または質問を入力してください。"
)
async def ai_command(interaction: discord.Interaction, prompt: str):
    """
    /ai [prompt] で呼び出され、システムプロンプトを使用してAIの応答を制御します。
    """
    user_info = f"ユーザー: {interaction.user.name} (ID: {interaction.user.id})"
    
    if not gemini_clients:
        await interaction.response.send_message(
            "❌ 応答可能なGemini APIキーが設定されていません。管理者にご連絡ください。", 
            ephemeral=True
        )
        await send_dm_log(f"**🚨 /ai コマンド失敗:** {user_info}\n理由: 有効なGeminiキーなし。")
        return

    await interaction.response.defer()
    
    gemini_text = None
    used_client_name = None
    
    # クライアントのリストを順に試行する
    for client_info in gemini_clients:
        client = client_info['client']
        used_client_name = client_info['name']
        
        try:
            # 必須: ユーザーの質問とシステムプロンプトの両方を設定
            contents = [
                {"role": "user", "parts": [{"text": prompt}]}
            ]
            
            log_info = f"INFO: {used_client_name} キーを使用してGemini APIを試行します..."
            print(log_info)
            await send_dm_log(f"**🟡 試行:** {user_info}\nキー: {used_client_name}\n質問: `{prompt[:100]}...`")
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=contents,
                # ★ 変更点: システムプロンプトを設定
                config={"system_instruction": AI_SYSTEM_PROMPT} 
            )
            
            gemini_text = response.text.strip()
            # 応答が成功したらループを抜ける
            break 

        except APIError as e:
            # APIエラー（レート制限など）が発生した場合
            log_warning = f"WARNING: {used_client_name} キーでAPIエラーが発生しました: {e}"
            print(log_warning)
            await send_dm_log(f"**⚠️ APIエラー:** {log_warning}\n次のキーにフォールバックします。")
            continue # 次のクライアントを試行
            
        except Exception as e:
            # その他の予期せぬエラー
            log_error = f"ERROR: {used_client_name} キーで予期せぬエラーが発生しました: {e}"
            print(log_error)
            await send_dm_log(f"**❌ 致命的エラー:** {log_error}")
            continue

    
    # 試行結果の処理
    if gemini_text:
        # 成功応答
        if len(gemini_text) > 2000:
            # メッセージが長すぎる場合は分割して送信
            initial_response = await interaction.followup.send(
                f"**質問:** {prompt}\n(キー: {used_client_name})\n\n**AI応答 (1/2):**\n{gemini_text[:1900]}..."
            )
            await interaction.channel.send(f"**AI応答 (2/2):**\n...{gemini_text[1900:]}")
            
            # 応答メッセージのリンクをDMログに保存
            message_link = initial_response.jump_url
            dm_log_message = f"**✅ 応答成功 (分割):** {user_info}\n使用キー: `{used_client_name}`\n[チャットリンク]({message_link})\n質問: `{prompt[:80]}...`"
            await send_dm_log(dm_log_message)
            
        else:
            # 通常の応答
            final_response = await interaction.followup.send(
                f"**質問:** {prompt}\n(キー: {used_client_name})\n\n**AI応答:**\n{gemini_text}"
            )
            
            # 応答メッセージのリンクをDMログに保存
            message_link = final_response.jump_url
            dm_log_message = f"**✅ 応答成功:** {user_info}\n使用キー: `{used_client_name}`\n[チャットリンク]({message_link})\n質問: `{prompt[:80]}...`"
            await send_dm_log(dm_log_message)
            
    else:
        # すべてのクライアントが失敗した場合
        await interaction.followup.send(
            "❌ すべてのGemini APIキーの試行に失敗しました。現在、レート制限などにより応答できません。",
            ephemeral=True
        )
        await send_dm_log(f"**🔴 応答失敗 (全キー):** {user_info}\n質問: `{prompt[:80]}...`\n理由: すべてのキーがAPIエラー。")


# ----------------------------------------------------------------------
# Webサーバーのセットアップ
# ----------------------------------------------------------------------

async def handle_ping(request):
    """Renderからのヘルスチェックに応答するハンドラー。"""
    
    JST = timezone(timedelta(hours=+9), 'JST')
    current_time_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S %Z")
    
    print(
        f"🌐 [Web Ping] 応答時刻: {current_time_jst} | "
        f"有効Geminiキー: {len(gemini_clients)}個 | "
        f"ステータス: OK"
    )

    return web.Response(text="Bot is running and ready for Gemini requests.")

def setup_web_server():
    """Webサーバーを設定し、CORSを適用する関数。"""
    app = web.Application()
    app.router.add_get('/', handle_ping)
    cors = aiohttp_cors.setup(app, defaults={"*": aiohttp_cors.ResourceOptions(allow_credentials=True, allow_methods=["GET"], allow_headers=("X-Requested-With", "Content-Type"),)})
    for route in list(app.router.routes()):
        cors.add(route)
    return app

async def start_web_server():
    """Webサーバーを非同期で起動する関数。"""
    web_app = setup_web_server()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    print(f"Webサーバーをポート {PORT} で起動します (Render対応)...")
    try:
        await site.start()
    except Exception as e:
        print(f"Webサーバーの起動に失敗しました: {e}")
    await asyncio.Future() 


async def main():
    """Discord BotとWebサーバーを同時に起動するメイン関数。"""
    
    web_server_task = asyncio.create_task(start_web_server())
    discord_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    
    await asyncio.gather(discord_task, web_server_task)


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot and Web Server stopped.")
    except Exception as e:
        print(f"メイン実行中に予期せぬエラーが発生しました: {e}")
