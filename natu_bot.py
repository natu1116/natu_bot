import asyncio
import os
import json
import aiohttp
from aiohttp import web
import aiohttp_cors 

# Google GenAI SDK
from google import genai
from google.genai.errors import APIError

# --- 環境設定 ---
# Renderの環境変数からAPIキーを取得
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    print("FATAL: GEMINI_API_KEY が環境変数に設定されていません。Gemini APIは動作しません。")

# Geminiクライアントの初期化 (APIキーは自動で環境変数から読み込まれます)
try:
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    # APIキーが空の場合などに発生。ログに出力するのみ。
    print(f"Gemini Clientの初期化中にエラーが発生しました: {e}")
    client = None


# ----------------------------------------------------------------------
# 1. API呼び出しハンドラーの定義
# ----------------------------------------------------------------------

async def handle_gemini_request(request):
    """
    POSTリクエストを受け取り、Geminiモデルに問い合わせて応答を返します。
    """
    if not client:
        return web.json_response({
            "error": "Gemini APIクライアントが初期化されていません。APIキーを確認してください。"
        }, status=500)

    try:
        # リクエストからJSONボディを読み込み、ユーザーのプロンプトを取得
        data = await request.json()
        prompt = data.get("prompt", "なぜ空は青いのですか？") 

        print(f"Geminiに問い合わせ中: {prompt[:30]}...")

        # --- Gemini APIの呼び出し ---
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt]
        )
        
        # 応答テキストを抽出
        gemini_text = response.text.strip()

        # 成功応答を返す
        return web.json_response({
            "prompt": prompt,
            "response": gemini_text,
            "model": response.model
        })

    except json.JSONDecodeError:
        return web.json_response({"error": "無効なJSON形式です。"}, status=400)
    except APIError as e:
        print(f"Gemini APIエラー: {e}")
        return web.json_response({"error": f"Gemini APIでの処理中にエラーが発生しました: {e}"}, status=503)
    except Exception as e:
        print(f"予期せぬエラー: {e}")
        return web.json_response({"error": f"サーバーエラー: {e}"}, status=500)


# ----------------------------------------------------------------------
# 2. WebサーバーのセットアップとCORSの適用
# ----------------------------------------------------------------------

async def handle_ping(request):
    """ヘルスチェック用のルート"""
    return web.Response(text="Bot is running and ready for Gemini requests.")

def setup_web_server():
    """
    Webサーバーを設定し、CORSを適用する関数。
    """
    app = web.Application()
    
    # ヘルスチェックルート
    app.router.add_get('/', handle_ping)
    
    # Gemini API呼び出し用のルート
    app.router.add_post('/gemini', handle_gemini_request)
    
    # CORS設定を適用 (すべてのオリジンからのアクセスを許可)
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            allow_methods=["GET", "POST"], # GETとPOSTの両方を許可
            allow_headers=("X-Requested-With", "Content-Type"),
        )
    })

    # すべてのルートにCORSを適用
    for route in list(app.router.routes()):
        cors.add(route)

    return app

# ----------------------------------------------------------------------
# 3. Bot本体の起動ロジック (簡略化)
# ----------------------------------------------------------------------

async def start_bot_and_server():
    web_app = setup_web_server()
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    # ポートは環境変数から取得
    port = int(os.environ.get("PORT", 8080)) 
    
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    print(f"Webサーバーをポート {port} で起動します...")
    await site.start()
    
    # サーバーを維持
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    # 実際のBotファイルでは、この関数を呼び出す必要があります
    asyncio.run(start_bot_and_server())
