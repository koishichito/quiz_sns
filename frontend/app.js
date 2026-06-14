/* ============================================================================
 * app.js — 画面操作(入力取得 → API/Keycloak 呼び出し → 結果描画)を担当
 *
 * 通信の詳細は api.js に任せ、ここは「いつ何を呼び、結果をどう見せるか」に集中。
 * api.js が先に読み込まれている前提(index.html の <script> の順序)。
 *
 * 【Keycloak 移行に伴う画面側の変更】
 *   ・ログインは login()(中身が Keycloak のトークン取得に変わった)を呼ぶだけ。
 *     画面ロジックからは「宛先が Keycloak になった」ことを意識しなくてよい
 *     ―― 関数の【契約】(username/password を渡せばトークンが保存される)が
 *     同じなので、呼び出し側は無傷。これが層を分ける利点。
 *   ・ログイン状態の表示(/auth/me)とログアウトを追加し、トークンが本当に
 *     このAPIで通用するか(= Keycloak と噛み合っているか)を画面で確認できるようにした。
 *   ・「登録」UI は無い。ユーザー登録は Keycloak の管理コンソール/アカウント画面の
 *     仕事になったため(このAPIに /auth/register はもう存在しない)。
 *
 * ────────────────────────────────────────────────────────────────────────
 * このファイルで登場する DOM / 配列まわりの記法
 * ────────────────────────────────────────────────────────────────────────
 * ● document.getElementById("id") … 要素取得。見つからなければ null。
 * ● 要素.value … <input>/<textarea> の入力値。型は常に文字列。
 * ● 要素.textContent = s … 中身をテキストとして設定(HTML 解釈されない=XSS安全)。
 *     対して innerHTML = s は s を HTML として解釈する → 未検証データを入れると穴。
 * ● 配列メソッドの連鎖
 *     split("\n") 改行で分割 / map(f) 各要素を変換 / filter(f) 残す要素を選ぶ /
 *     forEach(f) 各要素に実行(for 代わり)。f は (引数) => ... のアロー関数。
 * ● createElement("li") / 親.appendChild(子) … DOM を作って木に追加。
 * ● Number(s) … 文字列→数値。Number("")→0、Number("abc")→NaN。
 * ● s || null … 左が falsy("" など)なら右。未入力を null に倒すのに使う。
 * ● 要素.addEventListener("click", f) … クリック時に f を呼ぶ登録。
 * ────────────────────────────────────────────────────────────────────────
 */

// --- ログイン状態の表示更新 --------------------------------------------------
// トークンがあれば /auth/me を叩いて「ようこそ ○○」を出し、無ければ未ログイン表示。
// /auth/me が通る = Keycloak のトークンをこのAPIが受理した、という確認でもある。
async function refreshAuthStatus() {
  const statusEl = document.getElementById("auth-status");
  const token = getToken();
  if (!token) {
    statusEl.textContent = "未ログイン";
    return;
  }
  try {
    const me = await fetchMe();
    // me は UserRead(id / username / created_at)。username は Keycloak の
    // preferred_username の写し。textContent なので HTML 解釈されず安全。
    statusEl.textContent = `ログイン中: ${me.username}(id=${me.id})`;
  } catch (err) {
    // 401 等なら apiFetch がトークンを消している。未ログイン表示に戻す。
    statusEl.textContent = `未ログイン(${err.detail})`;
  }
}

// --- 一覧の取得と表示 --------------------------------------------------------
async function loadQuizzes() {
  const listEl = document.getElementById("quiz-list");
  try {
    // 一覧は認証不要。await で取得完了を待つ(戻り値は QuizRead の配列)。
    const quizzes = await apiFetch("/quizzes");

    // 既存の表示をクリア("" を入れるだけなので innerHTML でも安全)。
    listEl.innerHTML = "";

    quizzes.forEach((q) => {
      const li = document.createElement("li");
      // ユーザー入力(タイトル・名前)を表示する箇所なので textContent を使う(XSS防止)。
      // q.owner.username が引けるのは、API が owner をネストして返すから。
      li.textContent = `${q.title} — by ${q.owner.username}`;
      listEl.appendChild(li);
    });
  } catch (err) {
    listEl.textContent = `取得失敗: ${err.detail}`;
  }
}

// --- ログイン(Keycloak へ)--------------------------------------------------
async function handleLogin() {
  const username = document.getElementById("login-username").value;
  const password = document.getElementById("login-password").value;
  const msgEl = document.getElementById("login-msg");
  try {
    // login() の中身は Keycloak のトークン取得。成功するとトークンが保存される。
    await login(username, password);
    msgEl.textContent = "ログインしました";
    refreshAuthStatus(); // ヘッダの状態表示を更新(/auth/me で疎通確認)
  } catch (err) {
    // Keycloak の認証失敗は通常 401。error_description が detail に入っている。
    msgEl.textContent = `ログイン失敗: ${err.detail}`;
  }
}

// --- ログアウト --------------------------------------------------------------
function handleLogout() {
  logout(); // ローカルのトークンを捨てるだけ(このAPIはステートレス)
  document.getElementById("login-msg").textContent = "ログアウトしました";
  refreshAuthStatus();
}

// --- クイズ投稿 --------------------------------------------------------------
async function handleCreateQuiz() {
  const msgEl = document.getElementById("create-msg");

  // 選択肢は「1行 = 1選択肢」で textarea に入力させ、改行で分割して配列にする。
  const choices = document
    .getElementById("quiz-choices")
    .value
    .split("\n")              // 改行で分割 → ["選択肢1", "選択肢2", ...]
    .map((s) => s.trim())     // 各行の前後空白を除去(Windows 改行 \r もここで取れる)
    .filter((s) => s !== ""); // 空行を捨てる

  // 送信オブジェクト。キー名は API 側の QuizCreate スキーマと一致させる必要がある。
  const body = {
    title: document.getElementById("quiz-title").value,
    question: document.getElementById("quiz-question").value,
    choices: choices,
    // 入力は文字列なので Number で数値化。0始まりの正解番号。
    // 既知の穴: 欄が空だと Number("")=0、数字以外だと NaN(JSON では null に化け 422)。
    answer_index: Number(document.getElementById("quiz-answer").value),
    // 空欄は "" になるが、|| null で null に倒す(API では explanation は任意)。
    explanation: document.getElementById("quiz-explanation").value || null,
  };

  try {
    // 投稿は認証必須。apiFetch が保存済み(Keycloak由来)トークンを自動で添付する。
    // 未ログインなら 401、選択肢2未満や answer_index 範囲外なら 422 が返る。
    await apiFetch("/quizzes", { method: "POST", body });
    msgEl.textContent = "投稿しました";
    loadQuizzes(); // 一覧を更新
  } catch (err) {
    if (err.status === 401) {
      msgEl.textContent = "投稿にはログインが必要です(まず Keycloak でログイン)";
    } else if (err.status === 422) {
      // 422 の detail は文字列ではなく「違反箇所の配列」。そのまま文字列化して見せる。
      msgEl.textContent = `入力エラー: ${JSON.stringify(err.detail)}`;
    } else {
      msgEl.textContent = `投稿失敗: ${err.detail}`;
    }
  }
}

// --- ボタンとイベントの結びつけ ----------------------------------------------
// 関数そのものを渡す(handleLogin であって handleLogin() ではない)。
// () を付けると「今すぐ実行した結果(undefined)」を登録してしまい誤り。
document.getElementById("btn-login").addEventListener("click", handleLogin);
document.getElementById("btn-logout").addEventListener("click", handleLogout);
document.getElementById("btn-create").addEventListener("click", handleCreateQuiz);

// --- 初期化 ------------------------------------------------------------------
// 読み込み直後に一覧描画とログイン状態の確認を行う。
loadQuizzes();
refreshAuthStatus();
