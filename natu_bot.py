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
# 監視対象チャンネル一覧
# ---------------------------
monitoring_channels = set()
monitoring_log_channel_id = None
# ---------------------------
# --- 環境設定 ---
# ---------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY_PRIMARY = os.environ.get("GEMINI_API_KEY") # Primary Key
GEMINI_API_KEY_SECONDARY = os.environ.get("GEMINI_API_KEY_SECONDARY") # Secondary Key
GEMINI_API_KEY_THIRD = os.environ.get("GEMINI_API_KEY_THIRD") # Third Key
GEMINI_API_KEY_FOURTH = os.environ.get("GEMINI_API_KEY_FOURTH") # Fourth Key
TARGET_USER_ID_FOR_LOGS = int(os.environ.get("TARGET_USER_ID_FOR_LOGS", 0))

PORT = int(os.environ.get("PORT", 8080)) 

# 通知チャンネルIDの取得と変換
NOTIFICATION_CHANNEL_ID = os.environ.get("NOTIFICATION_CHANNEL_ID")
if NOTIFICATION_CHANNEL_ID:
    try:
        NOTIFICATION_CHANNEL_ID = int(NOTIFICATION_CHANNEL_ID)
    except ValueError:
        NOTIFICATION_CHANNEL_ID = None

# DMログの送信先ユーザーID (管理者向け通知先)
TARGET_USER_ID_FOR_LOGS = 1402481116723548330 

# ★ AIの接し方を定義するシステムプロンプト
AI_SYSTEM_PROMPT = (
    "あなたは、知識豊富で、フレンドリーかつ協力的、そして少しウィットに富んだアシスタントです。すべての質問に対して、"
    "簡潔で分かりやすい言葉で答えてください。専門的な用語を使う際は、必ず分かりやすい解説を加えてください。"
    "ユーザーの問いかけに対して、親しみやすいトーンで応じ、会話を楽しむように努めてください。"
    "なお、あなたは、ユーザーの問いかけに1度しか返す事ができないことを考えた返答をしてください。"
)

# ----------------------------------------------------------------------
# ★ 禁止ワードリスト (インメモリで管理)
# Bot再起動で初期値に戻ります。
# ----------------------------------------------------------------------
BANNED_WORDS = set([
    "あらし", "広告", "宣伝", "discord.gg", "https://discord.gg"
])

# ----------------------------------------------------------------------
# ★ メッセージレート制限設定とデータ構造
# ----------------------------------------------------------------------
# ユーザーごとのメッセージ投稿履歴を保持 {user_id: [timestamp1, timestamp2, ...]}
spam_tracking = {} 
# 1分間（60秒）に許容される最大メッセージ数
RATE_LIMIT_MESSAGES = 30
# レート制限をチェックする時間枠（秒）
RATE_LIMIT_WINDOW_SECONDS = 60
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# ★ 一時BAN管理用データ構造 (インメモリ)
# {guild_id: {user_id: unban_datetime_utc}}
# Bot再起動でリセットされる点に注意
# ----------------------------------------------------------------------
time_bans = {} 

# Botの設定 (Intentsの設定が必要)
# メンバーリストの取得とプレゼンス（ステータス）の取得のために、Intentを設定
intents = discord.Intents.default()
intents.message_content = True 
intents.members = True     # on_messageでメンバーの権限をチェックするために必要
intents.presences = True   # メンバーのオンライン状態（Botステータス確認）のために必要
intents.bans = True        # BAN/UNBAN操作のために必要
bot = commands.Bot(command_prefix='!', intents=intents)


# 利用可能なAPIキーのリスト
GEMINI_API_KEYS = [
    GEMINI_API_KEY_PRIMARY,
    GEMINI_API_KEY_SECONDARY,
    GEMINI_API_KEY_THIRD,
    GEMINI_API_KEY_FOURTH,
]
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key] # Noneや空文字列を除外

def get_gemini_client(api_key: str) -> genai.Client:
    """指定されたAPIキーでGeminiクライアントを作成する"""
    return genai.Client(api_key=api_key)

async def check_api_key_and_get_models(api_key: str) -> tuple[bool, Optional[list[str]]]:
    """
    APIキーの有効性をチェックし、有効な場合は利用可能なモデルのリストを取得する。
    
    NOTE: この関数はAPIキーが有効であるか（API呼び出しが可能か）をチェックするために利用します。
    """
    if not api_key:
        return False, None

    client = get_gemini_client(api_key)
    
    # モデルリストの取得を非同期で実行するために、スレッドプールエグゼキュータを使用する
    # Google GenAI SDKのlist_modelsは同期関数であるため
    loop = asyncio.get_event_loop()
    
    try:
        # list_modelsを非同期で実行
        # 接続が成功し、有効なキーであることを確認する
        models_response = await loop.run_in_executor(
            None, # デフォルトのスレッドプールエグゼキュータを使用
            client.models.list
        )
        
        # モデル名のみを抽出（ここでは使用しないが、成功の証拠として取得）
        model_names = [model.name for model in models_response]
        return True, model_names
        
    except APIError as e:
        # APIキーが無効、またはレートリミット超過などのエラーが発生した場合
        print(f"API Key Check Error: {e}")
        return False, None
    except Exception as e:
        # その他の予期せぬエラー
        print(f"Unexpected Error during API Key Check: {e}")
        return False, None

# --------------------------
# --- コマンド群: /genai コマンド ---
# --------------------------

@bot.tree.command(name="genai", description="Gemini APIキーの有効性を確認し、クォータの情報を表示します。")
async def genai_status(interaction: discord.Interaction):
    """Gemini APIキーの有効性、クォータに関する情報を表示します。"""
    
    await interaction.response.defer(ephemeral=True) # 時間がかかる可能性があるので遅延応答

    if not GEMINI_API_KEYS:
        embed = discord.Embed(
            title="Gemini APIステータス",
            description="⚠️ Gemini APIキーが設定されていません。環境変数を確認してください。",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    results = []
    # すべてのキーに対して非同期でチェックを実行
    tasks = [check_api_key_and_get_models(key) for key in GEMINI_API_KEYS]
    results = await asyncio.gather(*tasks)

    description = f"現在**{len(GEMINI_API_KEYS)}**個のキーが設定されています。\n\n"
    
    # API使用状況に関する注釈を追加
    quota_note = (
        "**【重要】残りの使用回数（クォータ）について:**\n"
        "Gemini APIのSDKでは、現在の**残りの使用回数を直接取得することはできません。**\n"
        "クォータの正確な情報は、Google AI StudioまたはGoogle Cloud Consoleの課金ダッシュボードでご確認ください。\n"
        "以下のステータスは、APIキーが現在**有効であり、認証に成功しているか**を示しています。\n"
        "レート制限に達した場合、Botはエラーを報告します。\n\n"
    )
    
    description += quota_note
    
    valid_key_count = 0
    
    for i, (is_valid, _) in enumerate(results): # モデルリストは使用しないため、_ で受け取る
        key_label = f"キー #{i + 1}"
        
        if is_valid:
            description += f"✅ **{key_label}**: **有効** (APIに接続成功)\n"
            valid_key_count += 1
        else:
            description += f"❌ **{key_label}**: **無効/認証失敗** (API接続エラー)\n"
            
    embed = discord.Embed(
        title="🤖 Gemini API 接続ステータス",
        description=description,
        color=discord.Color.blue() if valid_key_count > 0 else discord.Color.red()
    )

    await interaction.followup.send(embed=embed, ephemeral=True)

# ----------------------------------------------------------------------
# Geminiクライアントの初期化とフォールバックリストの作成
# ----------------------------------------------------------------------

# 利用可能なAPIキーのリストと名前
API_KEY_CONFIGS = [
    (GEMINI_API_KEY_PRIMARY, 'Primary'),
    (GEMINI_API_KEY_SECONDARY, 'Secondary'),
    (GEMINI_API_KEY_THIRD, 'Third'),
    (GEMINI_API_KEY_FOURTH, 'Fourth'),
]

gemini_clients = []

def initialize_gemini_clients():
    """設定されたAPIキーに基づいてGeminiクライアントを初期化し、リストに格納します。"""
    global gemini_clients
    clients = []
    
    for api_key, name in API_KEY_CONFIGS:
        if api_key:
            try:
                client = genai.Client(api_key=api_key)
                clients.append({'client': client, 'name': name})
                print(f"Gemini Client ({name}) の初期化に成功しました。")
            except Exception as e:
                print(f"WARNING: Gemini Client ({name}) の初期化に失敗しました: {e}")
            
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
# ★ 自動BAN解除タスク
# ----------------------------------------------------------------------

async def unban_user_after_delay(guild_id: int, user_id: int, delay_seconds: float):
    """指定された遅延後にユーザーのBANを解除するタスクです。"""
    # delay_seconds が負または0の場合は即時終了
    if delay_seconds <= 0:
        return
        
    try:
        await asyncio.sleep(delay_seconds)
        
        guild = bot.get_guild(guild_id)
        if not guild:
            print(f"ERROR: Guild ID {guild_id} が見つかりません。自動BAN解除できませんでした。")
            return
            
        user = discord.Object(id=user_id)
        
        # BANリストをチェックし、該当ユーザーがいれば解除
        try:
            await guild.fetch_ban(user)
        except discord.NotFound:
            # ユーザーがBANリストにいない場合は何もしない
            print(f"INFO: User ID {user_id} は既にBANリストにいませんでした。自動BAN解除処理をスキップ。")
            return

        # BANを解除
        await guild.unban(user, reason="自動タイムBAN解除")
        
        # ログと通知
        print(f"SUCCESS: User ID {user_id} のBANが {guild.name} で自動解除されました。")
        
        # DMログ通知
        embed = discord.Embed(
            title="✅ 自動タイムBAN解除ログ",
            description=f"ユーザーID `{user_id}` のBANがサーバー `{guild.name}` で自動解除されました。",
            color=discord.Color.green()
        )
        embed.add_field(name="解除されたユーザー", value=f"<@{user_id}>", inline=False)
        embed.add_field(name="BAN期間", value=f"{delay_seconds / 3600:.2f} 時間", inline=False)
        embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))

        await send_dm_log(f"**🟢 自動BAN解除:** User ID `{user_id}` のBANが自動解除されました。", embed=embed)
                           
        # 内部状態から削除
        if guild_id in time_bans and user_id in time_bans[guild_id]:
            del time_bans[guild_id][user_id]
            if not time_bans[guild_id]:
                del time_bans[guild_id]

    except discord.Forbidden:
        print(f"ERROR: 権限不足により User ID {user_id} の自動BAN解除に失敗しました。Botの「メンバーをBAN」権限を確認してください。")
    except Exception as e:
        print(f"FATAL ERROR: 自動BAN解除中に予期せぬエラーが発生しました: {e}")

from discord import app_commands

# ---------------------------
# /monitoring コマンド群
# ---------------------------

@bot.tree.command(name="monitoring_add", description="このチャンネルを監視対象に追加します。")
@app_commands.checks.has_permissions(administrator=True)
async def monitoring_add(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    monitoring_channels.add(channel_id)

    await interaction.response.send_message(
        f"👁‍🗨 このチャンネル（<#{channel_id}>）を **監視対象に追加**しました。",
        ephemeral=False
    )



@bot.tree.command(name="monitoring_remove", description="このチャンネルを監視対象から外します。")
@app_commands.checks.has_permissions(administrator=True)
async def monitoring_remove(interaction: discord.Interaction):
    channel_id = interaction.channel_id

    if channel_id in monitoring_channels:
        monitoring_channels.remove(channel_id)
        msg = "🗑 このチャンネルを監視対象から削除しました。"
    else:
        msg = "⚠ このチャンネルは監視対象ではありません。"

    await interaction.response.send_message(msg, ephemeral=False)


@bot.tree.command(name="monitoring_send", description="このチャンネルをログ送信先に設定します。")
@app_commands.checks.has_permissions(administrator=True)
async def monitoring_send(interaction: discord.Interaction):
    global monitoring_log_channel_id
    monitoring_log_channel_id = interaction.channel_id

    await interaction.response.send_message(
        f"📡 このチャンネル（{interaction.channel.mention}）を **ログ送信先** に設定しました。",
        ephemeral=False
    )

    # テスト用のログ送信（任意）
    embed = discord.Embed(
        title="📡 ログ送信先設定",
        description="このチャンネルがログ送信先として設定されました。",
        color=discord.Color.blue()
    )
    await interaction.channel.send(embed=embed)


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


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is None:
        return
    if message.channel.id not in monitoring_channels:
        return
    if monitoring_log_channel_id is None:
        return

    log_channel = bot.get_channel(monitoring_log_channel_id)
    if log_channel is None:
        return

    embed = discord.Embed(
        title="🗑 メッセージ削除",
        description=f"**ユーザー:** {message.author.mention}\n"
                    f"**元チャンネル:** {message.channel.mention}",
        color=discord.Color.red()
    )
    embed.add_field(name="内容", value=message.content or "（なし）", inline=False)
    embed.timestamp = message.created_at

    await log_channel.send(embed=embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot:
        return
    if before.guild is None:
        return
    if before.channel.id not in monitoring_channels:
        return
    if before.content == after.content:
        return
    if monitoring_log_channel_id is None:
        return

    log_channel = bot.get_channel(monitoring_log_channel_id)
    if log_channel is None:
        return

    # メッセージリンク生成
    message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{before.id}"

    embed = discord.Embed(
        title="✏ メッセージ編集",
        description=(
            f"**ユーザー:** {before.author.mention}\n"
            f"**元チャンネル:** {before.channel.mention}\n"
            f"**[メッセージリンク]({message_link})**"
        ),
        color=discord.Color.orange()
    )
    embed.add_field(name="編集前", value=before.content or "（なし）", inline=False)
    embed.add_field(name="編集後", value=after.content or "（なし）", inline=False)
    embed.timestamp = after.edited_at

    await log_channel.send(embed=embed)


# ----------------------------------------------------------------------
# ★ メッセージレート制限と禁止ワードチェック (統合・修正済み)
# ----------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    """メッセージが送信されたときに実行され、スパムチェックを行います。"""
    
    # 1. チェック対象外のメッセージを無視
    if message.author.bot:
        return
    
    # ギルド（サーバー）外のメッセージは無視（DMなど）
    if message.guild is None:
        await bot.process_commands(message)
        return
        
    # 2. 管理者権限チェック
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
        
        # 現在のメッセージのタイムスタンプを追加
        spam_tracking[user_id].append(now)

        time_limit = now - timedelta(seconds=RATE_LIMIT_WINDOW_SECONDS)
        # 60秒より古いメッセージ履歴を削除
        spam_tracking[user_id] = [
            ts for ts in spam_tracking[user_id] if ts > time_limit
        ]

        # 3. レート制限の確認 (30コメント/60秒を超過した場合)
        if len(spam_tracking[user_id]) > RATE_LIMIT_MESSAGES:
            try:
                # 4. スパムメッセージを一括削除
                # Botが「メッセージの管理」と「メッセージ履歴を読む」権限を持っているか確認
                perms = message.channel.permissions_for(message.guild.me)
                if perms.manage_messages and perms.read_message_history:
                    
                    messages_to_delete = []
                    
                    # タイムウィンドウ内のメッセージをフェッチして削除対象を特定
                    # limit=200で直近200件をチェックし、パフォーマンスと精度を両立
                    async for msg in message.channel.history(limit=200, after=time_limit):
                        if msg.author.id == user_id:
                            messages_to_delete.append(msg)
                    
                    # トリガーとなったメッセージが履歴に載っていなければ確実に追加
                    if message not in messages_to_delete:
                        messages_to_delete.append(message)
                    
                    # 削除対象を投稿が古い順にソート (delete_messagesの挙動のため)
                    messages_to_delete.sort(key=lambda m: m.created_at)

                    if messages_to_delete:
                        deleted_count = 0
                        # List comprehensionsでコンテンツを抽出
                        deleted_contents = [m.content for m in messages_to_delete]
                        
                        try:
                            # 2週間以内のメッセージを効率的に一括削除（100件まで）
                            if (datetime.now(timezone.utc) - messages_to_delete[0].created_at) < timedelta(days=14):
                                await message.channel.delete_messages(messages_to_delete)
                                deleted_count = len(messages_to_delete)
                            else:
                                # 2週間より古いメッセージが含まれる可能性がある場合は個別削除
                                for msg in messages_to_delete:
                                    await msg.delete()
                                    deleted_count += 1
                        except discord.Forbidden:
                             # 一括削除の権限がない場合、個別に削除を試みる
                             for msg in messages_to_delete:
                                 try:
                                     await msg.delete()
                                     deleted_count += 1
                                 except (discord.Forbidden, discord.HTTPException):
                                     continue
                        except Exception as http_e:
                            print(f"ERROR: メッセージの一括削除中に予期せぬHTTPエラーが発生しました: {http_e}")
                            pass

                        # 5. 警告メッセージの送信（メンション付き）
                        warning_text = (
                            f"🚨 **{message.author.mention}** さん、ご注意ください！\n"
                            f"短時間（{RATE_LIMIT_WINDOW_SECONDS}秒以内）に{RATE_LIMIT_MESSAGES}件以上のメッセージを投稿しました。\n"
                            f"スパム行為と見なされるため、**直近の{deleted_count}件のメッセージはすべて削除されました。**\n"
                            f"続けて投稿するとミュートなどの処置が取られる可能性があります。"
                        )
                        
                        await message.channel.send(warning_text, delete_after=15)
                        
                        # 6. 管理者へのログ送信
                        embed = discord.Embed(
                            title="💥 自動レート制限スパム一括削除ログ",
                            description=f"ユーザー **{message.author.mention}** がレート制限を超過したため、直近のメッセージを一括削除しました。",
                            color=discord.Color.brand_red()
                        )
                        embed.add_field(name="チャンネル", value=message.channel.mention, inline=False)
                        embed.add_field(name="送信者", value=f"{message.author.name} (ID: {message.author.id})", inline=False)
                        embed.add_field(name="超過回数", value=f"直近 {RATE_LIMIT_WINDOW_SECONDS}秒で {len(spam_tracking[user_id])} 回", inline=True)
                        embed.add_field(name="削除件数", value=f"{deleted_count} 件", inline=True)
                        
                        log_contents = "\n".join([f"`{c[:50]}...`" for c in deleted_contents[:5]])
                        embed.add_field(name="削除されたメッセージ (一部)", value=log_contents or "内容なし", inline=False)
                        
                        embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))
                        
                        await send_dm_log(f"**💥 レート超過一括削除:** {message.author.name} がスパム行為を行いました。", embed=embed)

                        # 履歴をリセットして、連鎖的な警告を防ぐ
                        spam_tracking[user_id] = []
                        
                        return # 削除されたため、以降の処理は不要
                    
                else:
                    print(f"ERROR: レート制限超過メッセージを削除または履歴を読む権限がありません。Botの権限を確認してください。")

            except discord.Forbidden:
                print(f"ERROR: レート制限超過メッセージの削除または警告の権限がありません。Botの権限を確認してください。")
            except Exception as e:
                print(f"ERROR: レート制限スパム処理中に予期せぬエラーが発生しました: {e}")


    # ----------------------------------------------------------------------
    # ★ 禁止ワードチェック（非管理者のみ）
    # ----------------------------------------------------------------------
    
    # グローバルで定義されたBANNED_WORDSリストを使用
    if not is_administrator and BANNED_WORDS:
        content_lower = message.content.lower()
        detected_word = None
        
        for word in BANNED_WORDS:
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
                    delete_after=10
                )

                # 管理者へのログ送信
                embed = discord.Embed(
                    title="メッセージ削除ログ (禁止ワード)",
                    description=f"ユーザー **{message.author.mention}** のメッセージが削除されました。",
                    color=discord.Color.red()
                )
                
                embed.add_field(name="チャンネル", value=message.channel.mention, inline=False)
                embed.add_field(name="送信者", value=f"{message.author.name} (ID: {message.author.id})", inline=False)
                embed.add_field(name="検出ワード", value=f"`{detected_word}`", inline=False)
                # メッセージ内容を埋め込みに直接格納（最大1024文字）
                content_preview = message.content[:1000] + ('...' if len(message.content) > 1000 else '')
                embed.add_field(name="削除されたメッセージ内容", value=content_preview, inline=False)
                
                await send_dm_log(f"**🔴 自動削除 (禁止ワード):** {message.author.name} が禁止ワードを使用しました。", embed=embed)
                
                return # 削除が成功したので、以降の処理は不要

            except discord.Forbidden:
                print(f"ERROR: メッセージ削除の権限がありません。Botの権限を確認してください。")
            except Exception as e:
                print(f"ERROR: メッセージの自動削除中に予期せぬエラーが発生しました: {e}")

    # スラッシュコマンドやその他の通常のコマンド処理
    await bot.process_commands(message)


# ----------------------------------------------------------------------
# ★ コマンド: /timeban (一時BAN) - 新規追加
# ----------------------------------------------------------------------

@bot.tree.command(name="timeban", description="指定したユーザーを指定した時間（時間）BANします。")
@discord.app_commands.describe(
    member="一時的にBANするメンバーを選択してください。",
    hours="BANする時間（整数、1時間以上）を入力してください。"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def timeban_command(interaction: discord.Interaction, member: discord.Member, hours: int):
    
    await interaction.response.defer(ephemeral=True)

    # 1. バリデーションチェック
    if hours <= 0 or hours > 7 * 24: # 1時間以上、7日以内を推奨
        await interaction.followup.send(
            "❌ BAN時間は1時間以上、168時間（7日間）以内の整数で指定してください。",
            ephemeral=True
        )
        return
        
    if not interaction.guild.me.guild_permissions.ban_members:
        await interaction.followup.send(
            "❌ Botに「メンバーをBAN」権限がありません。Botのロール権限を確認してください。",
            ephemeral=True
        )
        return
        
    # 2. ロール階層チェック
    if interaction.guild.owner_id == member.id or interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send(
            f"❌ {member.mention} さんの最高ロールはBotの最高ロールより高いか同等です。BotではBANできません。",
            ephemeral=True
        )
        return

    delay_seconds = hours * 3600
    unban_time_utc = datetime.now(timezone.utc) + timedelta(hours=hours)
    unban_time_jst = unban_time_utc.astimezone(timezone(timedelta(hours=+9), 'JST'))
    
    guild_id = interaction.guild_id
    user_id = member.id
    
    # 既存のBANタスクが存在するかチェック（再実行防止と上書き）
    if guild_id in time_bans and user_id in time_bans[guild_id]:
        await interaction.followup.send(
            f"⚠️ {member.mention} さんは既に一時BAN中です。新しいBAN期間で上書きします。",
            ephemeral=True
        )
        # 既存のタイマーをキャンセルする処理があれば理想的だが、今回は簡易実装のため省略
        
    try:
        # 3. ユーザーをBAN
        ban_reason = f"一時BAN ({hours}時間, 実行者: {interaction.user.name})"
        await interaction.guild.ban(member, reason=ban_reason, delete_message_days=0)

        # 4. 自動UNBANタスクをスケジュール
        asyncio.create_task(
            unban_user_after_delay(guild_id, user_id, delay_seconds)
        )
        
        # 5. 内部状態を更新
        if guild_id not in time_bans:
            time_bans[guild_id] = {}
            
        time_bans[guild_id][user_id] = unban_time_utc # UTC時刻で保存

        # 6. 成功メッセージをチャンネルに送信
        await interaction.followup.send(
            f"🚨 **{member.mention}** さんを **{hours} 時間**（`{unban_time_jst.strftime('%m/%d %H:%M:%S JST')}`）BANしました。\n"
            f"時間が経過すると自動的にBANが解除されます。",
            ephemeral=False
        )
        
        # 7. 管理者へのログ送信 (DM)
        embed = discord.Embed(
            title="🚫 一時BAN実行ログ",
            description=f"実行者: {interaction.user.mention} (ID: {interaction.user.id})",
            color=discord.Color.red()
        )
        embed.add_field(name="対象メンバー", value=f"{member.name} (ID: {member.id})", inline=False)
        embed.add_field(name="BAN期間", value=f"{hours} 時間", inline=True)
        embed.add_field(name="自動解除予定時刻 (JST)", value=unban_time_jst.strftime('%Y/%m/%d %H:%M:%S'), inline=True)
        embed.set_footer(text="Bot再起動時は自動解除タイマーがリセットされます。")
        embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))

        await send_dm_log(f"**🔴 メンバー一時BAN:** {member.name} が {hours} 時間BANされました。", embed=embed)

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ BANに失敗しました。Botに「メンバーをBAN」権限があること、およびBotの最高ロールが**ターゲットメンバーの最高ロールより上**にあることを確認してください。",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ BAN中に予期せぬエラーが発生しました: {e}",
            ephemeral=True
        )


# ----------------------------------------------------------------------
# コマンドグループ: /name (ニックネーム管理)
# ----------------------------------------------------------------------

# name コマンドグループを定義
name_group = discord.app_commands.Group(name="name", description="メンバーのニックネーム管理コマンド（管理者専用）")
bot.tree.add_command(name_group)


# ----------------------------------------------------------------------
# サブコマンド: /name set (ニックネーム設定)
# ----------------------------------------------------------------------
@name_group.command(name="set", description="メンバーのニックネームを新しい値に設定します。")
@discord.app_commands.describe(
    member="ニックネームを変更したいメンバーを選択してください。",
    nickname="新しく設定するニックネーム。"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def name_set_command(interaction: discord.Interaction, member: discord.Member, nickname: str):
    
    await interaction.response.defer(ephemeral=True)

    # 1. Botが「ニックネームの管理」権限を持っているか確認
    if not interaction.guild.me.guild_permissions.manage_nicknames:
        await interaction.followup.send(
            "❌ Botに「ニックネームの管理」権限がありません。Botのロール権限を確認してください。",
            ephemeral=True
        )
        return

    # 2. ターゲットメンバーがサーバーオーナーであるかチェック
    if interaction.guild.owner_id == member.id:
        await interaction.followup.send(
            f"❌ **サーバーオーナー**である {member.mention} さんのニックネームはBotでは変更できません。",
            ephemeral=True
        )
        return

    # 3. Botの最高ロールが対象メンバーの最高ロールより厳密に上にあるかチェック
    if interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send(
            f"❌ Botの最高ロールが {member.mention} さんの最高ロールより**低いか同等**です。Discordのロール設定でBotのロールを**ターゲットメンバーのロールより上に**配置してください。",
            ephemeral=True
        )
        return

    try:
        # ニックネームを変更
        old_nickname = member.nick if member.nick else member.name
        await member.edit(nick=nickname)

        # 成功メッセージ
        await interaction.followup.send(
            f"✅ {member.mention} さんのニックネームを「**{old_nickname}**」から「**{nickname}**」に変更しました。",
            ephemeral=False # 全員に表示
        )
        
        # 管理者へのログ送信 (DM)
        embed = discord.Embed(
            title="👤 ニックネーム設定ログ",
            description=f"実行者: {interaction.user.mention} (ID: {interaction.user.id})",
            color=discord.Color.blue()
        )
        embed.add_field(name="チャンネル", value=interaction.channel.mention, inline=False)
        embed.add_field(name="対象メンバー", value=f"{member.name} (ID: {member.id})", inline=False)
        embed.add_field(name="変更前ニックネーム", value=old_nickname, inline=True)
        embed.add_field(name="変更後ニックネーム", value=nickname, inline=True)
        embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))

        await send_dm_log(f"**🔷 ニックネーム設定:** {member.name} のニックネームが設定されました。", embed=embed)

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ ニックネームの変更に失敗しました。Botは「ニックネームの管理」権限を持ち、かつBotの最高ロールが**ターゲットメンバーの最高ロールより上**にあることを再度確認してください。",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"❌ ニックネームの変更中にHTTPエラーが発生しました: {e}",
            ephemeral=True
        )
        
# ----------------------------------------------------------------------
# サブコマンド: /name reset (ニックネームリセット)
# ----------------------------------------------------------------------
@name_group.command(name="reset", description="メンバーのニックネームをリセット（初期化）します。")
@discord.app_commands.describe(
    member="ニックネームをリセットしたいメンバーを選択してください。"
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def name_reset_command(interaction: discord.Interaction, member: discord.Member):
    
    await interaction.response.defer(ephemeral=True)

    # 1. Botが「ニックネームの管理」権限を持っているか確認
    if not interaction.guild.me.guild_permissions.manage_nicknames:
        await interaction.followup.send(
            "❌ Botに「ニックネームの管理」権限がありません。Botのロール権限を確認してください。",
            ephemeral=True
        )
        return

    # 2. ターゲットメンバーがサーバーオーナーであるかチェック
    if interaction.guild.owner_id == member.id:
        await interaction.followup.send(
            f"❌ **サーバーオーナー**である {member.mention} さんのニックネームはBotではリセットできません。",
            ephemeral=True
        )
        return

    # 3. Botの最高ロールが対象メンバーの最高ロールより厳密に上にあるかチェック
    if interaction.guild.me.top_role <= member.top_role:
        await interaction.followup.send(
            f"❌ Botの最高ロールが {member.mention} さんの最高ロールより**低いか同等**です。Discordのロール設定でBotのロールを**ターゲットメンバーのロールより上に**配置してください。",
            ephemeral=True
        )
        return
        
    # 4. そもそもニックネームが設定されているかチェック
    old_nickname = member.nick
    if old_nickname is None:
        await interaction.followup.send(
            f"⚠️ {member.mention} さんには現在ニックネームが設定されていません。リセットの必要はありません。",
            ephemeral=True
        )
        return

    try:
        # ニックネームをリセット (nick=Noneでサーバーでのニックネームを解除)
        await member.edit(nick=None)

        # 成功メッセージ
        await interaction.followup.send(
            f"✅ {member.mention} さんのニックネーム「**{old_nickname}**」をリセットし、ユーザー名（`{member.name}`）に戻しました。",
            ephemeral=False # 全員に表示
        )
        
        # 管理者へのログ送信 (DM)
        embed = discord.Embed(
            title="🔄 ニックネームリセットログ",
            description=f"実行者: {interaction.user.mention} (ID: {interaction.user.id})",
            color=discord.Color.orange()
        )
        embed.add_field(name="チャンネル", value=interaction.channel.mention, inline=False)
        embed.add_field(name="対象メンバー", value=f"{member.name} (ID: {member.id})", inline=False)
        embed.add_field(name="変更前ニックネーム", value=old_nickname, inline=True)
        embed.add_field(name="変更後ニックネーム", value="ユーザー名にリセット", inline=True)
        embed.timestamp = datetime.now(timezone(timedelta(hours=+9), 'JST'))

        await send_dm_log(f"**🔄 ニックネームリセット:** {member.name} のニックネームがリセットされました。", embed=embed)

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ ニックネームのリセットに失敗しました。Botは「ニックネームの管理」権限を持ち、かつBotの最高ロールが**ターゲットメンバーの最高ロールより上**にあることを再度確認してください。",
            ephemeral=True
        )
    except discord.HTTPException as e:
        await interaction.followup.send(
            f"❌ ニックネームのリセット中にHTTPエラーが発生しました: {e}",
            ephemeral=True
        )

# ----------------------------------------------------------------------
# スラッシュコマンド: /bot (Botステータス確認)
# ----------------------------------------------------------------------
@bot.tree.command(name="bot", description="サーバーに存在するBotのオンライン状態を確認します。")
async def bot_status_command(interaction: discord.Interaction):
    
    await interaction.response.defer() # 処理に時間がかかる可能性があるためdefer
    
    # サーバーの全メンバーを取得（Botを含む）
    # .membersはキャッシュされたメンバーリストを使用
    bot_members = [
        member for member in interaction.guild.members if member.bot
    ]
    
    if not bot_members:
        await interaction.followup.send("このサーバーにはBotが存在しません。")
        return

    # ステータスごとのアイコンと名前を格納する辞書
    status_map = {
        discord.Status.online: "🟢 **[オンライン]**",
        discord.Status.idle: "🌙 **[退席中]**",
        discord.Status.dnd: "🔴 **[取り込み中]**",
        discord.Status.offline: "⚫ **[オフライン]**",
        discord.Status.invisible: "⚫ **[オフライン]**",
    }
    
    # Botリストをステータス（オンライン順）でソート
    # Sort order: online > dnd > idle > offline
    def sort_key(member):
        status_order = {
            discord.Status.online: 0,
            discord.Status.dnd: 1,
            discord.Status.idle: 2,
            discord.Status.offline: 3,
            discord.Status.invisible: 3,
        }
        return status_order.get(member.status, 4) # 未知のステータスは最後

    sorted_bots = sorted(bot_members, key=sort_key)
    
    # 結果の文字列を生成
    bot_list_lines = []
    for bot_member in sorted_bots:
        # ニックネームがあればニックネーム、なければユーザー名を使用
        display_name = bot_member.nick if bot_member.nick else bot_member.name
        
        # ステータスアイコンを取得
        status_icon = status_map.get(bot_member.status, "⚪ **[不明]**")
        
        bot_list_lines.append(f"{status_icon} `{display_name}`")

    # 応答メッセージの作成
    # Embedを使用して見やすく整形
    embed = discord.Embed(
        title=f"🤖 このサーバーのBotステータス (現在 {len(bot_members)} 件)",
        description="\n".join(bot_list_lines),
        color=discord.Color.blue()
    )
    embed.set_footer(text="オンライン状態はDiscordのステータスに基づいています。")
    
    await interaction.followup.send(embed=embed)


# ----------------------------------------------------------------------
# ★ コマンドグループ: /blockword (禁止ワード管理)
# ----------------------------------------------------------------------

# blockword コマンドグループを定義
blockword_group = discord.app_commands.Group(name="blockword", description="サーバーの禁止ワードリストを管理します（管理者専用）")
bot.tree.add_command(blockword_group)

# ----------------------------------------------------------------------
# サブコマンド: /blockword add (禁止ワード追加)
# ----------------------------------------------------------------------
@blockword_group.command(name="add", description="禁止ワードリストに新しい単語を追加します。")
@discord.app_commands.describe(word="禁止したい単語（大文字小文字は区別されません）。")
@discord.app_commands.checks.has_permissions(administrator=True)
async def blockword_add_command(interaction: discord.Interaction, word: str):
    global BANNED_WORDS
    
    # 小文字にして、前後の空白を削除
    word_lower = word.lower().strip()
    
    if not word_lower:
        await interaction.response.send_message("❌ 追加する禁止ワードを入力してください。", ephemeral=True)
        return

    if word_lower in BANNED_WORDS:
        await interaction.response.send_message(f"⚠️ `{word}` はすでに禁止ワードリストに存在しています。", ephemeral=True)
    else:
        BANNED_WORDS.add(word_lower)
        await interaction.response.send_message(
            f"✅ 禁止ワードリストに `{word_lower}` を追加しました。\n現在のリスト件数: {len(BANNED_WORDS)}", 
            ephemeral=True
        )
        await send_dm_log(f"**➕ 禁止ワード追加:** 管理者 {interaction.user.name} により `{word_lower}` が追加されました。")

# ----------------------------------------------------------------------
# サブコマンド: /blockword remove (禁止ワード削除)
# ----------------------------------------------------------------------
@blockword_group.command(name="remove", description="禁止ワードリストから単語を削除します。")
@discord.app_commands.describe(word="削除したい禁止ワード。")
@discord.app_commands.checks.has_permissions(administrator=True)
async def blockword_remove_command(interaction: discord.Interaction, word: str):
    global BANNED_WORDS
    
    # 小文字にして、前後の空白を削除
    word_lower = word.lower().strip()

    if word_lower in BANNED_WORDS:
        BANNED_WORDS.remove(word_lower)
        await interaction.response.send_message(
            f"✅ 禁止ワードリストから `{word_lower}` を削除しました。\n現在のリスト件数: {len(BANNED_WORDS)}", 
            ephemeral=True
        )
        await send_dm_log(f"**➖ 禁止ワード削除:** 管理者 {interaction.user.name} により `{word_lower}` が削除されました。")
    else:
        await interaction.response.send_message(f"⚠️ `{word}` は禁止ワードリストに存在しません。", ephemeral=True)

# ----------------------------------------------------------------------
# サブコマンド: /blockword list (禁止ワード表示)
# ----------------------------------------------------------------------
@blockword_group.command(name="list", description="現在の禁止ワードリストを表示します。")
@discord.app_commands.checks.has_permissions(administrator=True)
async def blockword_list_command(interaction: discord.Interaction):
    
    if not BANNED_WORDS:
        await interaction.response.send_message("現在の禁止ワードリストは空です。", ephemeral=True)
        return
        
    # リストをソートして表示用に整形
    sorted_words = sorted(list(BANNED_WORDS))
    
    # 応答メッセージの作成
    word_list_text = "\n".join([f"- `{word}`" for word in sorted_words])
    
    embed = discord.Embed(
        title=f"🛑 現在の禁止ワードリスト ({len(BANNED_WORDS)} 件)",
        description=word_list_text,
        color=discord.Color.red()
    )
    embed.set_footer(text="Botが再起動されると、リストは初期設定値に戻ります。")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ----------------------------------------------------------------------
# コマンドエラーハンドリング (MissingPermissionsを処理)
# ----------------------------------------------------------------------
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    if isinstance(error, discord.app_commands.MissingPermissions):
        # 権限がない場合のエラー処理
        await interaction.response.send_message(
            "❌ あなたにはこのコマンドを実行するための**管理者権限**がありません。",
            ephemeral=True
        )
        print(f"WARNING: 権限のないユーザー {interaction.user.name} が {interaction.command.name} を実行しようとしました。")
    else:
        # その他のエラーはデフォルトで処理
        print(f"ERROR: コマンドエラーが発生しました: {error}")
        try:
            # すでに応答しているか確認
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"❌ コマンドの実行中に予期せぬエラーが発生しました: {type(error).__name__}",
                    ephemeral=True
                )
            else:
                 await interaction.response.send_message(
                    f"❌ コマンドの実行中に予期せぬエラーが発生しました: {type(error).__name__}",
                    ephemeral=True
                )
        except Exception:
            # 応答に失敗した場合
            pass


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
    
    # クライアントのリストを順に試行する（フォールバック）
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
# Webサーバーのセットアップ (ヘルスチェック用)
# ----------------------------------------------------------------------

async def handle_ping(request):
    """RenderなどのPaaS環境からのヘルスチェックに応答するハンドラー。"""
    
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
    
    # Discord Botのトークンが設定されているか確認
    if not DISCORD_TOKEN:
        print("FATAL ERROR: DISCORD_TOKEN が設定されていません。Botを起動できません。")
        return

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
