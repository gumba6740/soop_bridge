// 관리자 브라우저 표시. 관리자 브라우저에서는 VOD를 열어도 조회수를 올리지 않는다.
//
// 켜기: 아무 페이지 주소 뒤에 ?admin=1 을 붙여 한 번 연다 → 이 브라우저에 기억됨
// 끄기: ?admin=0
//
// 보안 장치가 아니다. 서버는 누가 관리자인지 모르고, 프론트가 "조회수 요청을 보낼지"만 정한다.

const KEY = "soop_bridge_admin";

/** 주소의 ?admin= 값을 읽어 기억해 둔다. 앱 시작 때 한 번 부른다. */
export function syncAdminFlag(): void {
  const value = new URLSearchParams(window.location.search).get("admin");
  try {
    if (value === "1") localStorage.setItem(KEY, "1");
    if (value === "0") localStorage.removeItem(KEY);
  } catch {
    // 사생활 보호 모드 등에서 localStorage가 막혀 있으면 그냥 넘어간다
  }
}

export function isAdmin(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}