"""認証系エンドポイント: 現在は「自分が誰か」の確認だけ。

━━ /register と /login はどこへ行った? ━━━━━━━━━━━━━━━━
Keycloak へ移管された。ユーザー登録もログイン(トークン発行)も
Keycloak の仕事になったので、アプリからは該当エンドポイントごと消えた。
コードが消える = パスワード保管・総当たり対策・ユーザー列挙攻撃対策などの
【責任ごと】消える。「認証を外部IdPへ委譲する」価値はこの削除diffそのもの。

トークンの取得先(実体はKeycloakのエンドポイント。アプリは関与しない):
  POST {KEYCLOAK_SERVER_URL}/realms/{realm}/protocol/openid-connect/token
  ボディ(フォーム形式):
    grant_type=password&client_id=...&username=...&password=...

━━ /auth/me を残す(新設する)理由 ━━━━━━━━━━━━━━━━━━━━
「配られたトークンが本当にこのAPIで通用するか」
「アプリDB上で自分がどの行に対応づいたか(JITプロビジョニング結果)」
を確認するための疎通テスト用エンドポイント。
認証構成を変えた直後のデバッグで、最初に叩く場所になる。
"""
from fastapi import APIRouter, Depends

from .. import models, schemas
from ..auth import get_current_user

# APIRouter = エンドポイントを束ねる部品。main.py の include_router で本体へ合流する。
# prefix="/auth" がこのファイル内の全パスの頭に付く(下の "/me" は /auth/me になる)。
# tags は Swagger UI 上のグルーピング表示用
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=schemas.UserRead)
def read_me(current_user: models.User = Depends(get_current_user)):
    # get_current_user が「トークン検証 → JITプロビジョニング → User取得」まで
    # 全部終えているので、本体は受け取ったものを返すだけ。
    # response_model=UserRead のフィルタにより keycloak_sub は外に出ない
    return current_user
