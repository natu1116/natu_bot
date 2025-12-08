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

# ★ AIの接し方を定義するシステムプロンプト
AI_SYSTEM_PROMPT = (
    "あなたは、知識豊富で、フレンドリーかつ協力的、そして少しウィットに富んだアシスタントです。すべての質問に対して、"
    "簡潔で分かりやすい言葉で答えてください。専門的な用語を使う際は、必ず分かりやすい解説を加えてください。"
    "ユーザーの問いかけに対して、親しみやすいトーンで応じ、会話を楽しむように努めてください。"
    "なお、この会話は、ユーザーの問いかけに1度しか返す事ができないことを考えた返答をしてください。"
)

# ★ Botの設定（禁止ワードリストなど）
BOT_CONFIG = {
    # 検出したいスパム/禁止ワードのリスト（小文字で定義することを推奨）
    "BANNED_WORDS": ["あらし", "広告", "宣伝", "discord.gg", "https://discord.gg"], 
    "MODERATION_LOG_CHANNEL": NOTIFICATION_CHANNEL_ID # 削除ログの送信先チャンネル（通知チャンネルを流用）
}

# ----------------------------------------------------------------------
# ★ メッセージレート制限設定とデータ構造
# ----------------------------------------------------------------------
# ユーザーごとのメッセージ投稿履歴を保持 {user_id: [timestamp1, timestamp2, ...]}
# ユーザーごとの制限です
spam_tracking = {} 
# 1分間（60秒）に許容される最大メッセージ数
RATE_LIMIT_MESSAGES = 30
# レート制限をチェックする時間枠（秒）
RATE_LIMIT_WINDOW_SECONDS = 60
# ----------------------------------------------------------------------


# Botの設定 (Intentsの設定が必要)
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True # on_messageでメンバーの権限をチェックするために必要
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

# ----------------------------------------------------------------------
# ★ メッセージレート制限と禁止ワードチェック
# ----------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    """メッセージが送信されたときに実行され、スパムチェックを行います。"""
    
    # 1. チェック対象外のメッセージを無視
    # Bot自身のメッセージは無視
    if message.author.bot:
        return
    
    # DMでのメッセージは無視（サーバー内でのスパム対策のため）
    if message.guild is None:
        return
        
    # 2. 管理者権限チェック
    # メッセージ送信者が管理者権限を持っている場合は無視
    is_administrator = message.author.guild_permissions.administrator
    
    # ----------------------------------------------------------------------
    # ★ ユーザーごとのレート制限スパムチェック（非管理者のみ）
    # ----------------------------------------------------------------------
    if not is_administrator:
        now = datetime.now(timezone.utc)
        user_id = message.author.id

        # 投稿履歴の更新と古いタイムスタンプの削除
        if user_id not in spam_tracking:
            spam_tracking[user_id] = []
        
        spam_tracking[user_id].append(now)

        time_limit = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        # 60秒より古いメッセージ履歴を削除
        spam_tracking[user_id] = [
            ts for ts in spam_tracking[user_id] if ts > time_limit
        ]

        # 3. レート制限の確認 (30コメント/60秒を超過した場合)
        if len(spam_tracking[user_id]) > RATE_LIMIT_MESSAGES:
            try:
                # 4. スパムメッセージを削除
                if message.channel.permissions_for(message.guild.me).manage_messages:
                    await message.delete()
                    
                    # 5. 警告メッセージの送信（メンション付き）
                    warning_text = (
                        f"🚨 **{message.author.mention}** さん、ご注意ください！\n"
                        f"短時間（{RATE_LIMIT_WINDOW_SECONDS}秒以内）に{RATE_LIMIT_MESSAGES}件以上のメッセージを投稿しました。\n"
                        f"スパム行為と見なされるため、このメッセージは削除されました。続けて投稿するとミュートなどの処置が取られる可能性があります。"
                    )
                    
                    # 警告メッセージをチャンネルに送信 (15秒後に自動削除)
                    await message.channel.send(warning_text, delete_after=15)
                    
                    # 6. 管理者へのログ送信
                    embed = discord.Embed(
                        title="💥 自動レート制限スパム削除ログ",
                        description=f"ユーザー **{message.author.mention}** がレート制限を超過したため、メッセージを削除し警告しました。",
                        color=discord.Color.brand_red()
                    )
                    embed.add_field(name="チャンネル", value=message.channel.mention, inline=False)
                    embed.add_field(name="送信者", value=f"{message.author.name} (ID: {message.author.id})", inline=False)
                    embed.add_field(name="超過回数", value=f"直近 {RATE_LIMIT_WINDOW_SECONDS}秒で {len(spam_tracking[user_id])} 回", inline=False)
                    embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))
                    
                    # DMログと、可能であれば設定されたチャンネルにも送信
                    await send_dm_log(f"**💥 レート超過:** {message.author.name} がスパム行為を行いました。", embed=embed)

                    # スパム判定が確定したら、そのユーザーの履歴をリセットして、連鎖的な警告を防ぐ
                    spam_tracking[user_id] = []
                    
                    # 削除された場合は、以降の処理（禁止ワードチェックやコマンド処理）は不要
                    return 

                else:
                    print(f"ERROR: レート制限超過メッセージを削除する権限がありません。Botの権限を確認してください。")

            except discord.Forbidden:
                print(f"ERROR: レート制限超過メッセージの削除または警告の権限がありません。Botの権限を確認してください。")
            except Exception as e:
                print(f"ERROR: レート制限スパム処理中に予期せぬエラーが発生しました: {e}")


    # ----------------------------------------------------------------------
    # ★ 既存の禁止ワードチェック（非管理者のみ）
    # ----------------------------------------------------------------------
    
    # レート制限で削除されなかった、かつ非管理者のメッセージに対してのみ実行
    if not is_administrator:
        content_lower = message.content.lower()
        detected_word = None
        
        for word in BOT_CONFIG["BANNED_WORDS"]:
            if word in content_lower:
                detected_word = word
                break
                
        # 禁止ワードが検出された場合の処理
        if detected_word:
            try:
                # メッセージを削除
                await message.delete()
                print(f"MOD: スパムメッセージを削除しました。ユーザー: {message.author.name}, チャンネル: {message.channel.name}, 検出ワード: {detected_word}")
                
                # 削除されたことをユーザーに通知（任意）
                await message.channel.send(
                    f"🚨 **{message.author.mention}** さんのメッセージは不適切な内容（検出ワード: `{detected_word}`）を含むため自動的に削除されました。",
                    delete_after=10 # 10秒後に警告メッセージも自動削除
                )
                
                # 管理者へのログ送信
                embed = discord.Embed(
                    title="🗑️ 自動メッセージ削除ログ (禁止ワード)",
                    description=f"ユーザー **{message.author.mention}** のメッセージが削除されました。",
                    color=discord.Color.red()
                )
                embed.add_field(name="チャンネル", value=message.channel.mention, inline=False)
                embed.add_field(name="送信者", value=f"{message.author.name} (ID: {message.author.id})", inline=False)
                embed.add_field(name="検出ワード", value=f"`{detected_word}`", inline=False)
                embed.add_field(name="削除されたメッセージ内容", value=f")
                # DMログと、可能であれば設定されたチャンネルにも送信
                await send_dm_log(f"**🔴 自動削除:** {message.author.name} が禁止ワード `{detected_word}` を投稿しました。", embed=embed)
                
                # 削除された場合は、以降の処理（コマンド処理）は不要
                return

            except discord.Forbidden:
                print(f"ERROR: メッセージ削除の権限がありません。Botの権限を確認してください。")
            except Exception as e:
                print(f"ERROR: メッセージの自動削除中に予期せぬエラーが発生しました: {e}")
            
    # スラッシュコマンドやその他の通常のコマンド処理のために、
    # 最後に必ず `await bot.process_commands(message)` を呼び出す必要があります。
    await bot.process_commands(message)


# ----------------------------------------------------------------------
# スラッシュコマンド (/ai)
# ----------------------------------------------------------------------

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
                # ★ システムプロンプトを設定
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
