import discord
from discord.ext import commands
import os
from dotenv import load_dotenv # 💡 この行を追加

# 環境変数を読み込む (.envファイルから)
load_dotenv() 

# --- 設定するロールID ---
# リアクションを付けることができる権限ロール
AUTH_ROLE_ID = 1432204508536111155 
# 付与するロール
GRANT_ROLE_ID = 1432204383529078935
# 監視するリアクション絵文字
TARGET_EMOJI = '✅' # Unicode絵文字

# BotのIntentsを設定
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    """BotがDiscordに接続したときに実行されます"""
    print('-------------------------------------')
    print(f'Botがログインしました: {bot.user}')
    print('-------------------------------------')

@bot.event
async def on_raw_reaction_add(payload):
    """
    リアクションが追加されたときに実行されます。
    """

    if payload.user_id == bot.user.id:
        return

    if str(payload.emoji) != TARGET_EMOJI:
        return

    if payload.guild_id is None:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    # リアクションを付けたメンバー（リアクター）を取得
    reactor_member = guild.get_member(payload.user_id)
    if reactor_member is None:
        return

    # 権限ロールを持っているかを確認
    auth_role = discord.utils.get(guild.roles, id=AUTH_ROLE_ID)
    
    if auth_role is None or auth_role not in reactor_member.roles:
        return

    # リアクションが付いたメッセージを取得
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

    # コメントをしたユーザー（ターゲット）を取得
    target_user = message.author
    
    if target_user.bot or target_user is None:
        return

    # ターゲットユーザーに付与するロールを取得
    grant_role = discord.utils.get(guild.roles, id=GRANT_ROLE_ID)

    if grant_role is None:
        print(f"エラー: 付与ロールID {GRANT_ROLE_ID} が見つかりませんでした。")
        return

    # ターゲットユーザーにロールを付与
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


# --- Botの起動 ---
# 💡 環境変数 'TOKEN' からトークンを取得
BOT_TOKEN = os.getenv('TOKEN') 

if not BOT_TOKEN:
    print("⚠️ エラー: 環境変数 'TOKEN' が設定されていません。'.env'ファイルを確認してください。")
else:
    bot.run(BOT_TOKEN)