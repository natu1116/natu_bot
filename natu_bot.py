import os
import discord
from discord.ext import commands # commands.Botを使う
import asyncio

# Gemini APIクライアント
from google import genai
from google.genai.errors import APIError

# --- 環境設定 ---
# 環境変数からトークンとAPIキーを取得
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not DISCORD_TOKEN:
    print("FATAL: DISCORD_TOKEN が環境変数に設定されていません。Botは起動できません。")
if not GEMINI_API_KEY:
    print("FATAL: GEMINI_API_KEY が環境変数に設定されていません。Gemini APIは動作しません。")


# Botの設定 (Intentsの設定が必要)
intents = discord.Intents.default()
intents.message_content = True # メッセージの内容を読み取る権限を有効にする

# commands.Botとしてクライアントを初期化
# command_prefixはスラッシュコマンドを使う場合は特に重要ではありませんが、設定しておきます。
bot = commands.Bot(command_prefix='!', intents=intents)

try:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEYが設定されていません。")
        
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    print("Gemini Client の初期化に成功しました。")

except Exception as e:
    print(f"Gemini Clientの初期化中にエラーが発生しました: {e}")
    gemini_client = None

# ----------------------------------------------------------------------
# Discordイベントハンドラー
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

# ----------------------------------------------------------------------
# スラッシュコマンド /ai の定義
# ----------------------------------------------------------------------

@bot.tree.command(name="ai", description="Gemini AIに質問を送信します。")
@discord.app_commands.describe(
    prompt="AIに話したい内容、または質問を入力してください。"
)
async def ai_command(interaction: discord.Interaction, prompt: str):
    """
    /ai [prompt] で呼び出され、Gemini APIの応答を返すコマンド。
    """
    if not gemini_client:
        await interaction.response.send_message(
            "❌ Gemini APIが初期化されていません。管理者にご連絡ください。", 
            ephemeral=True # ephemeral=True は、メッセージをコマンド実行者のみに表示します
        )
        return

    # 応答生成中の通知を送信（Discordに即座に応答する必要があります）
    # `thinking` ステータスを表示
    await interaction.response.defer()
    
    try:
        # 質問内容
        user_prompt = f"ユーザーからの質問/要求：{prompt}"
        
        # 1. Gemini APIの呼び出し
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[user_prompt]
        )
        
        # 2. 回答テキストを抽出
        gemini_text = response.text.strip()
        
        # 3. Discordに回答を送信 (編集して表示)
        
        # 応答が2000文字を超える場合は分割
        if len(gemini_text) > 2000:
            # 最初の2000文字を送信
            await interaction.followup.send(
                f"**質問:** {prompt}\n\n**AI応答 (1/2):**\n{gemini_text[:1900]}..."
            )
            # 残りの部分を送信
            remaining_text = gemini_text[1900:]
            await interaction.channel.send(f"**AI応答 (2/2):**\n...{remaining_text}")
        else:
            # 応答を編集し、回答を表示
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
# 5. Botの起動
# ----------------------------------------------------------------------

async def main():
    """Botを起動するメイン関数。"""
    try:
        # Botを起動します
        await bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        print("FATAL: Discordトークンが無効です。Botを起動できません。")
    except Exception as e:
        print(f"Botの起動中にエラーが発生しました: {e}")


if __name__ == '__main__':
    asyncio.run(main())
