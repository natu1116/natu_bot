import os
import discord
from discord.ext import commands
import asyncio
import aiohttp
from aiohttp import web
import aiohttp_cors 

# Gemini APIクライアント
from google import genai
from google.genai.errors import APIError

# ---------------------------
# --- 環境設定 ---
# ---------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 8080)) # Renderが要求するポート

# Botの設定 (Intentsの設定が必要)
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# Geminiクライアントの初期化
gemini_client = None
try:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEYが設定されていません。")
        
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("Gemini Client の初期化に成功しました。")

except Exception as e:
    print(f"Gemini Clientの初期化中にエラーが発生しました: {e}")

# ----------------------------------------------------------------------
# Discordイベントとスラッシュコマンド (前回のコードを維持)
# ----------------------------------------------------------------------

@bot.event
async def on_ready():
    """BotがDiscordに接続したときに実行されます。"""
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    
    # スラッシュコマンドをDiscordサーバーに同期します
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)}個のコマンドを同期しました。")
    except Exception as e:
        print(f"コマンドの同期中にエラーが発生しました: {e}")
        
    print('------')


@bot.tree.command(name="ai", description="Gemini AIに質問を送信します。")
@discord.app_commands.describe(
    prompt="AIに話したい内容、または質問を入力してください。"
)
async def ai_command(interaction: discord.Interaction, prompt: str):
    """/ai [prompt] で呼び出され、Gemini APIの応答を返すコマンド。"""
    if not gemini_client:
        await interaction.response.send_message(
            "❌ Gemini APIが初期化されていません。管理者にご連絡ください。", 
            ephemeral=True
        )
        return

    await interaction.response.defer()
    
    try:
        user_prompt = f"ユーザーからの質問/要求：{prompt}"
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[user_prompt]
        )
        
        gemini_text = response.text.strip()
        
        if len(gemini_text) > 2000:
            await interaction.followup.send(
                f"**質問:** {prompt}\n\n**AI応答 (1/2):**\n{gemini_text[:1900]}..."
            )
            remaining_text = gemini_text[1900:]
            await interaction.channel.send(f"**AI応答 (2/2):**\n...{remaining_text}")
        else:
            await interaction.followup.send(
                f"**質問:** {prompt}\n\n**AI応答:**\n{gemini_text}"
            )

    except APIError as e:
        print(f"Gemini APIエラー: {e}")
        await interaction.followup.send(
            "❌ Gemini APIの呼び出し中にエラーが発生しました。時間を置いて再度お試しください。",
            ephemeral=True
        )
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        await interaction.followup.send(
            "❌ Bot側で予期せぬエラーが発生しました。",
            ephemeral=True
        )

# ----------------------------------------------------------------------
# Webサーバーのセットアップ (Renderの要求を満たすため)
# ----------------------------------------------------------------------

async def handle_ping(request):
    """Renderからのヘルスチェックに応答するハンドラー。"""
    return web.Response(text="Bot is running and ready for Gemini requests.")

def setup_web_server():
    """
    Webサーバーを設定し、CORSを適用する関数。
    """
    app = web.Application()
    
    # Renderはルート '/' へのGETリクエストをチェックします
    app.router.add_get('/', handle_ping)
    
    # CORS設定は不要ですが、念のため残しておきます
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            allow_methods=["GET"], 
            allow_headers=("X-Requested-With", "Content-Type"),
        )
    })

    for route in list(app.router.routes()):
        cors.add(route)

    return app

async def start_web_server():
    """Webサーバーを非同期で起動する関数。"""
    web_app = setup_web_server()
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    # 環境変数 PORT を使用
    site = web.TCPSite(runner, host='0.0.0.0', port=PORT)
    print(f"Webサーバーをポート {PORT} で起動します (Render対応)...")
    try:
        await site.start()
    except Exception as e:
        print(f"Webサーバーの起動に失敗しました: {e}")
    
    # サーバーを維持
    await asyncio.Future() # サーバーが停止しないように待機

# ----------------------------------------------------------------------
# 5. BotとWebサーバーの同時起動
# ----------------------------------------------------------------------

async def main():
    """Discord BotとWebサーバーを同時に起動するメイン関数。"""
    
    # Discord Botの起動タスク
    discord_task = bot.start(DISCORD_TOKEN)
    
    # Webサーバーの起動タスク
    web_server_task = start_web_server()
    
    # 両方のタスクが終了するまで待機（実際にはBotまたはWebサーバーが停止するまで）
    await asyncio.gather(discord_task, web_server_task)


if __name__ == '__main__':
    # asyncio.runで両方のタスクを開始
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot and Web Server stopped.")
    except Exception as e:
        print(f"メイン実行中に予期せぬエラーが発生しました: {e}")
