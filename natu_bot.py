import os
import discord
from discord.ext import commands
import asyncio
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

# 通知チャンネルIDの取得と変換 (変更なし)
NOTIFICATION_CHANNEL_ID = os.environ.get("NOTIFICATION_CHANNEL_ID")
if NOTIFICATION_CHANNEL_ID:
    try:
        NOTIFICATION_CHANNEL_ID = int(NOTIFICATION_CHANNEL_ID)
    except ValueError:
        NOTIFICATION_CHANNEL_ID = None


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
# Discordイベントとスラッシュコマンド
# ----------------------------------------------------------------------

@bot.event
async def on_ready():
    """BotがDiscordに接続したときに実行されます。"""
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    # 1. コマンドの同期
    try:
        synced = await bot.tree.sync()
        print(f"DEBUG: {len(synced)}個のコマンドを同期しました。")
    except Exception as e:
        print(f"DEBUG: コマンドの同期中にエラーが発生しました: {e}")
        
    # 2. ログイン通知の送信 (変更なし)
    if NOTIFICATION_CHANNEL_ID:
        try:
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            JST = timezone(timedelta(hours=+9), 'JST')
            current_time_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S %Z")
            
            if channel:
                embed = discord.Embed(
                    title="🤖 Botが正常に起動しました",
                    description=f"環境変数 **PORT {PORT}** でWebサーバーが稼働中です。\n**有効なGeminiキー: {len(gemini_clients)}個**",
                    color=discord.Color.green()
                )
                embed.add_field(name="接続ユーザー", value=f"{bot.user.name} (ID: {bot.user.id})", inline=False)
                embed.add_field(name="時刻 (JST)", value=current_time_jst, inline=False)
                
                await channel.send(embed=embed)
                print(f"DEBUG: ログイン通知をチャンネル {NOTIFICATION_CHANNEL_ID} に送信しました。")
            else:
                print(f"DEBUG: ID {NOTIFICATION_CHANNEL_ID} のチャンネルはBotが参加しているサーバーで見つかりませんでした。")
        
        except Exception as e:
            print(f"DEBUG: ログイン通知の送信中にエラーが発生しました: {e}")
            
    print('------')


@bot.tree.command(name="ai", description="Gemini AIに質問を送信します。")
@discord.app_commands.describe(
    prompt="AIに話したい内容、または質問を入力してください。"
)
async def ai_command(interaction: discord.Interaction, prompt: str):
    """
    /ai [prompt] で呼び出され、複数のAPIキーを順に試行して応答を返すコマンド。
    """
    if not gemini_clients:
        await interaction.response.send_message(
            "❌ 応答可能なGemini APIキーが設定されていません。管理者にご連絡ください。", 
            ephemeral=True
        )
        return

    await interaction.response.defer()
    
    gemini_text = None
    used_client_name = None
    
    # クライアントのリストを順に試行する
    for client_info in gemini_clients:
        client = client_info['client']
        used_client_name = client_info['name']
        
        try:
            user_prompt = f"ユーザーからの質問/要求：{prompt}"
            print(f"INFO: {used_client_name} キーを使用してGemini APIを試行します...")
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[user_prompt]
            )
            
            gemini_text = response.text.strip()
            # 応答が成功したらループを抜ける
            break 

        except APIError as e:
            # APIエラー（レート制限など）が発生した場合
            print(f"WARNING: {used_client_name} キーでAPIエラーが発生しました: {e} -> 次のキーにフォールバックします...")
            continue # 次のクライアントを試行
            
        except Exception as e:
            # その他の予期せぬエラー
            print(f"ERROR: {used_client_name} キーで予期せぬエラーが発生しました: {e}")
            continue

    
    # 試行結果の処理
    if gemini_text:
        # 成功応答
        if len(gemini_text) > 2000:
            await interaction.followup.send(
                f"**質問:** {prompt}\n(キー: {used_client_name})\n\n**AI応答 (1/2):**\n{gemini_text[:1900]}..."
            )
            remaining_text = gemini_text[1900:]
            await interaction.channel.send(f"**AI応答 (2/2):**\n...{remaining_text}")
        else:
            await interaction.followup.send(
                f"**質問:** {prompt}\n(キー: {used_client_name})\n\n**AI応答:**\n{gemini_text}"
            )
    else:
        # すべてのクライアントが失敗した場合
        await interaction.followup.send(
            "❌ すべてのGemini APIキーの試行に失敗しました。現在、レート制限などにより応答できません。",
            ephemeral=True
        )


# ----------------------------------------------------------------------
# Webサーバーのセットアップ (変更なし)
# ----------------------------------------------------------------------

async def handle_ping(request):
    """Renderからのヘルスチェックに応答するハンドラー。"""
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
