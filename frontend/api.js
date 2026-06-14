/* ============================================================================
 * api.js — バックエンドAPI / Keycloak との通信を一手に引き受けるモジュール
 *
 * 【重要な変更点】認証を「自前 → Keycloak」へ移したのに合わせて作り直した。
 *   旧版: login() は このAPI の /auth/login(フォーム)を叩いてトークンを得ていた。
 *   新版: login() は 【Keycloak】のトークンエンドポイントを直接叩く。
 *         このAPIにはもう /auth/login も /auth/register も存在しない
 *         (登録・ログイン・トークン発行はすべて Keycloak の仕事になった)。
 *   apiFetch(このAPIへの通常リクエスト)は Bearer トークンを添える部分が
 *   そのまま流用できるので、ロジックは変えていない。
 *
 * ────────────────────────────────────────────────────────────────────────
 * 認証の流れ(OpenID Connect / Keycloak)
 * ────────────────────────────────────────────────────────────────────────
 *   (1) ブラウザ ──username/password──▶ Keycloak のトークンURL
 *   (2) ブラウザ ◀──access_token(RS256署名のJWT)── Keycloak
 *   (3) ブラウザ ──Authorization: Bearer <token>──▶ このAPI
 *   (4) このAPI は Keycloak の公開鍵(JWKS)で署名を検証して受け入れる
 *
 *   ※ ここで使っているのは OAuth2 の「Direct Access Grant(パスワードグラント)」。
 *     README / Swagger / curl 例と同じ方式で、学習用に最短の構成。
 *     本番では「ブラウザがパスワードを直接握らない」Authorization Code + PKCE
 *     (Keycloak のログイン画面へリダイレクトする方式)が推奨。切り替えるときは
 *     この login() を keycloak-js 等のリダイレクト処理に差し替える(末尾の注記参照)。
 *
 * ────────────────────────────────────────────────────────────────────────
 * このファイル(と app.js)で登場する JavaScript の記法
 * ※ Python と挙動が違う箇所を中心に。ここを読めば下の本体が逐語的に追える
 * ────────────────────────────────────────────────────────────────────────
 * ● const / let /(var は使わない)
 *     const = 「再代入できない束縛」。値そのものが不変という意味ではない
 *             (const obj のあと obj.x = 1 は可。obj = {} だけが不可)。
 *     let   = 再代入できる束縛。const/let はブロック {} スコープ。
 * ● アロー関数   () => 式
 *     本体が「式1つ」なら return を書かずにその値を返す(暗黙の return)。
 * ● テンプレートリテラル   `文字 ${式} 文字`(Python の f"..." 相当)。
 * ● async / await と Promise
 *     async 関数は必ず Promise を返す。await は中身が確定するまで待って取り出す。
 *     fetch(...) も res.json() も Promise を返すので await が2回出てくる。
 * ● truthy / falsy(Python と違うので最注意)
 *     falsy は false, 0, ""(空文字), null, undefined, NaN の6つだけ。
 *     空配列 [] と空オブジェクト {} は JS では truthy。
 * ● null と undefined は別物
 *     undefined=値がまだ無い / null=「意図的に空」と人が入れた値。
 *     body !== undefined は「引数が省略されたか否か」だけを判定している。
 * ● === / !==(厳密等価)。型変換せずに比較。2 === "2" は false。常にこちらを使う。
 * ● オブジェクト分割代入つき引数 + デフォルト値
 *     f(path, { method = "GET", body } = {}) … 第2引数省略時は {} とみなす保険つき。
 * ● オブジェクト省略記法   { username, password } = { username: username, ... }。
 * ● class … extends … / constructor / super
 *     派生クラスでは this を触る前に必ず super() を呼ぶ規則がある。
 * ● ファイルをまたいだ関数の共有(import が無い理由)
 *     index.html が <script src="api.js"> → <script src="app.js"> の順で読み、
 *     どちらもクラシックスクリプト=1つのグローバルスコープを共有するので、
 *     api.js で定義した login / apiFetch を app.js から import 無しで呼べる。
 * ────────────────────────────────────────────────────────────────────────
 */

// === 接続先の設定(環境に合わせてここだけ直す)===============================
// ★ KEYCLOAK_* は、このAPIの .env(KEYCLOAK_SERVER_URL / KEYCLOAK_REALM /
//   KEYCLOAK_CLIENT_ID)と【必ず同じ値】にすること。
//   理由: フロントが取りに行く Keycloak と、API が信用している Keycloak(issuer)が
//   食い違うと、せっかく得たトークンを API 側が「別realmの物」として弾く。
//
// 末尾にスラッシュを付けないのは、後で `${BASE}${path}` と連結するとき path 側が
// "/quizzes" のように / で始まるため(付けると "...//quizzes" と二重になる)。

// このAPI(FastAPI)の場所。CORS の都合上、フロントは API 側 main.py の
// allow_origins(既定: http://localhost:5173 / :3000)から配信すること。
//   例) frontend/ で  python -m http.server 5173  → http://localhost:5173 で開く
// LAN の別マシンに置くなら "http://10.0.0.20:8000" のように書き換える
// (その場合 API 側 CORS の allow_origins にもそのオリジンを足す)。
const API_BASE = "http://localhost:8000";

// Keycloak 本体の場所 / realm / クライアントID。.env と一致させる(上記★)。
const KEYCLOAK_BASE = "http://localhost:8080";
const KEYCLOAK_REALM = "quiz-sns";
const KEYCLOAK_CLIENT_ID = "quiz-sns-api"; // パブリッククライアント(client_secret 不要)

// トークン発行エンドポイント(ログインの宛先は【このAPIではなく Keycloak】)。
// config.py の keycloak_token_url と同じ式を JS 側でも組み立てている。
const KEYCLOAK_TOKEN_URL =
  `${KEYCLOAK_BASE}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token`;

// === トークン(JWT)の保管 ====================================================
// localStorage = ブラウザに文字列を永続保存する箱(タブを閉じても残る)。
//   ・保存できるのは文字列だけ / getItem はキーが無ければ null を返す。
// セキュリティ上のトレードオフ: localStorage は JS から読めるため、XSS を踏むと
// トークンを盗まれ得る。学習用途では許容するが、性質は理解しておくこと。
const TOKEN_KEY = "quiz_token";

const getToken = () => localStorage.getItem(TOKEN_KEY);
const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
const clearToken = () => localStorage.removeItem(TOKEN_KEY);

// === エラーを表す独自クラス ===================================================
// fetch は「サーバが 404 や 401 を返した」だけでは例外を投げない(通信自体は成功)。
// 例外を投げるのはネットワーク断など "通信が成立しなかった" 場合だけ。
// そこで「HTTP ステータスが失敗系なら自分で投げる」ための例外型を定義し、
// status(数値)と detail(理由)を載せて画面側で出し分け可能にする。
class ApiError extends Error {
  constructor(status, detail) {
    // detail は文字列のことも配列(422の検証エラー)のこともあるので、
    // 文字列ならそのまま、そうでなければ JSON 文字列化して人が読める形にする。
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status; // 例: 401 / 404 / 422
    this.detail = detail; // 例: "クイズが見つかりません" / [{loc:.., msg:..}, ...]
  }
}

// === このAPI への共通通信処理(GET / POST / DELETE すべてここを通る)=========
// 引数:
//   path  … "/quizzes" など。API_BASE からの相対パス
//   第2引数 … { method, body }(両方省略可。method 既定は "GET")
async function apiFetch(path, { method = "GET", body } = {}) {
  const headers = {};

  // body を渡したときだけ「中身は JSON だ」と宣言する。
  // !== undefined にしているのは、空文字 "" や 0 を body にしても誤検知しないため。
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  // ログイン済み(トークンがある)なら Authorization ヘッダを足す。
  // これが API 側の Depends(get_current_user) を通過するための鍵。
  // 形式は厳密に  Authorization: Bearer <トークン>。
  // ―― Keycloak 移行後もこの部分は一切変わらない。トークンの【出所】が
  //    このAPIから Keycloak に変わっただけで、添え方は同じ。
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  // 204 No Content = 成功したが本文なし(DELETE 成功時)。json() 前に return。
  if (res.status === 204) {
    return null;
  }

  // 応答本文(JSON文字列)を JS オブジェクトへ変換。空 or 壊れた JSON なら null。
  const data = await res.json().catch(() => null);

  // res.ok = ステータスが 200〜299 なら true。失敗系なら例外を投げる。
  if (!res.ok) {
    // 401 はトークン無効/期限切れ。保存トークンを消して未ログイン状態へ戻す。
    if (res.status === 401) {
      clearToken();
    }
    // FastAPI はエラー理由を { "detail": ... } で返す。
    throw new ApiError(res.status, data && data.detail ? data.detail : "リクエストに失敗しました");
  }

  return data;
}

// === ログイン(宛先は Keycloak)==============================================
// 旧版は このAPI の /auth/login(OAuth2PasswordRequestForm)へフォームを送っていた。
// 新版は 【Keycloak】のトークンエンドポイントへ直接フォームを送る。
//   grant_type=password & client_id=... & username=... & password=...
// 形式は application/x-www-form-urlencoded(OAuth2 のトークンリクエスト仕様)。
// ここで JSON を送ると Keycloak が受け付けない ―― 最も高確率で踏む罠。
//
// ※ ブラウザ→Keycloak は別オリジンへの通信なので、Keycloak 側のクライアント設定
//   「Web origins」に、このフロントの配信元(例: http://localhost:5173)を
//   加えておかないと CORS で応答を読めずに失敗する(README 参照)。
async function login(username, password) {
  const res = await fetch(KEYCLOAK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    // URLSearchParams が "grant_type=...&client_id=..." の形に組み立て、
    // 値を自動でパーセントエンコードしてくれる。
    body: new URLSearchParams({
      grant_type: "password",
      client_id: KEYCLOAK_CLIENT_ID,
      username,   // オブジェクト省略記法(= username: username)
      password,
    }),
  });

  // Keycloak のエラーは { error, error_description } の形(FastAPI の detail とは別)。
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail =
      (data && (data.error_description || data.error)) || "ログインに失敗しました";
    throw new ApiError(res.status, detail);
  }

  // 成功 → アクセストークンを保存。以後 apiFetch が自動で添付する。
  // (refresh_token も data に入っているが、学習用なのでここでは使わない)
  setToken(data.access_token);
}

// === ログアウト ==============================================================
// このAPIにはセッションが無い(ステートレス)ので、ローカルのトークンを捨てるだけ。
// 厳密に Keycloak 側のセッションも切るなら logout エンドポイントを叩くが、
// パスワードグラントの学習構成では通常そこまでしない。
function logout() {
  clearToken();
}

// === 自分が誰かの確認(疎通テスト)===========================================
// /auth/me を叩くと「トークン検証 → JITプロビジョニング(初回はusers行を自動作成)
// → 自分のUser」が返る。ログイン状態の表示や、構成変更直後のデバッグに使う。
async function fetchMe() {
  return apiFetch("/auth/me");
}

/* ── 参考: 本番向け(Authorization Code + PKCE)へ切り替えるには ──────────────
 *   1) Keycloak のクライアントを「Standard flow 有効 / Direct access grants 無効」に。
 *      Valid redirect URIs にこのフロントのURLを登録。
 *   2) keycloak-js を読み込み、login() / logout() / fetchMe() を keycloak-js の
 *      init({onLoad:'login-required'}) と keycloak.token に置き換える。
 *   3) apiFetch は getToken() を keycloak.token から取るよう変えるだけでよい。
 *   ―― apiFetch(=このAPIとの契約)を境界にしてあるので、認証方式を替えても
 *      app.js 側(画面ロジック)は無傷で済む、という設計。
 */
