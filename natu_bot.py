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
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
PORT = int(os.environ.get("PORT", 8080)) 

# 通知チャンネルIDを環境変数から取得
# 必ずint型に変換してください
NOTIFICATION_CHANNEL_ID = os.environ.get("NOTIFICATION_CHANNEL_ID")
if NOTIFICATION_CHANNEL_ID:
    try:
        NOTIFICATION_CHANNEL_ID = int(NOTIFICATION_CHANNEL_ID)
    except ValueError:
        print("WARNING: NOTIFICATION_CHANNEL_IDが数値ではありません。通知機能は無効になります。")
        NOTIFICATION_CHANNEL_ID = None
else:
    print("WARNING: NOTIFICATION_CHANNEL_IDが設定されていません。通知機能は無効になります。")


# Botの設定 (Intentsの設定が必要)
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)

# Geminiクライアントの初期化
gemini_client = None
try:
    if GEMINI_API_KEY:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Gemini Clientの初期化中にエラーが発生しました: {e}")


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
        
    # 2. ログイン通知の送信 --- デバッグ強化開始 ---
    print(f"DEBUG: NOTIFICATION_CHANNEL_ID (数値変換後): {NOTIFICATION_CHANNEL_ID}")
    
    if NOTIFICATION_CHANNEL_ID:
        try:
            # Botがチャンネル情報を取得するのを少し待ちます（キャッシュ対策）
            await asyncio.sleep(5) 
            
            channel = bot.get_channel(NOTIFICATION_CHANNEL_ID)
            
            if channel:
                # チャンネルが見つかった場合
                print(f"DEBUG: チャンネルが見つかりました -> {channel.name} (サーバー: {channel.guild.name})")

                # JSTでの現在時刻を取得
                JST = timezone(timedelta(hours=+9), 'JST')
                current_time_jst = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S %Z")
                
                embed = discord.Embed(
                    title="🤖 Botが正常に起動しました",
                    description=f"環境変数 **PORT {PORT}** でWebサーバーが稼働中です。",
                    color=discord.Color.green()
                )
                embed.add_field(name="接続ユーザー", value=f"{bot.user.name} (ID: {bot.user.id})", inline=False)
                embed.add_field(name="時刻 (JST)", value=current_time_jst, inline=False)
                
                await channel.send(embed=embed)
                print(f"DEBUG: ログイン通知をチャンネル {NOTIFICATION_CHANNEL_ID} に送信しました。")
            else:
                # チャンネルが見つからなかった場合
                print(f"DEBUG: ID {NOTIFICATION_CHANNEL_ID} のチャンネルはBotが参加しているサーバーで見つかりませんでした。")
        
        except Exception as e:
            # 通知送信中の予期せぬエラー
            print(f"DEBUG: ログイン通知の送信中にエラーが発生しました: {e}")
            
    else:
        print("DEBUG: NOTIFICATION_CHANNEL_IDが設定されていない、または数値変換に失敗したため、通知はスキップされました。")
            
    print('------')

# (以下、ai_command, handle_ping, setup_web_server, start_web_server, main関数は変更なし)

@bot.tree.command(name="ai", description="Gemini AIに質問を送信します。")
@discord.app_commands.describe(
    prompt="AIに話したい内容、または質問を入力してください。"
)
async def ai_command(interaction: discord.Interaction, prompt: str):
    """/ai [prompt] で呼び出され、Gemini APIの応答を返すコマンド。 (変更なし)"""
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
