"use strict";
const $ = (s, root = document) => root.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[
        c
      ])
  );
const statusNames = {
  draft: "설정 중",
  running: "평가 진행 중",
  completed: "검토 대기",
  finalized: "확정 완료",
  failed: "실행 실패",
  cancelled: "취소됨",
};
const dimensionNames = {
  quality: "산출물 품질",
  complexity: "문제 난이도",
  validation: "검증 완성도",
  collaboration: "협업 전달력",
};
const state = {
  meta: null,
  members: [],
  evaluations: [],
  view: "overview",
  evaluation: null,
  editor: null,
  selectedMember: null,
  selectedProject: null,
  timer: null,
  pollEpoch: 0,
};
const uid = () =>
  "p-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 6);
const score = (v) => (v == null ? "—" : Number(v).toFixed(1));
const badge = (st) =>
  `<span class="pill ${esc(st)}"><i class="dot"></i>${esc(
    statusNames[st] || st
  )}</span>`;
let toastTimer;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("show"), 5000);
}
async function api(path, method = "GET", data) {
  const response = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-Review-Request": "1" },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  let body;
  try {
    body = await response.json();
  } catch (e) {
    throw new Error("서버 응답을 읽을 수 없습니다.");
  }
  if (!response.ok) {
    if (response.status === 401 && path != "/login") {
      loginView();
    }
    const detail = body.detail;
    throw new Error(
      Array.isArray(detail)
        ? detail.map((e) => e.msg).join("\n")
        : detail || "요청을 처리할 수 없습니다."
    );
  }
  return body;
}
async function refresh() {
  [state.members, state.evaluations] = await Promise.all([
    api("/members"),
    api("/evaluations"),
  ]);
}
function busy() {
  return state.evaluations.some((e) => e.status === "running");
}
function demoBanner() {
  return state.meta.demo
    ? '<div class="banner"><strong>DEMO WORKSPACE</strong> &nbsp; 합성 자료와 모의 LLM을 사용하는 검증 환경입니다. 표시된 점수는 실제 인사 평가에 사용할 수 없습니다.</div>'
    : "";
}
function shell(content) {
  const labels = {
    overview: "평가 대시보드",
    members: "팀원 관리",
    rubric: "평가 기준",
    editor: "평가 설정",
    detail: "평가 결과",
    services: "서비스 연결",
  };
  $(
    "#app"
  ).innerHTML = `<div class="layout"><aside class="sidebar"><div class="brand"><span class="brandmark">T</span>trace<span style="color:#b4d491">.</span></div><div class="brand-note">CONTRIBUTION INTELLIGENCE</div><div class="nav-caption">WORKSPACE</div><nav class="nav" aria-label="주 메뉴"><button data-action="nav" data-view="overview" class="${
    ["overview", "editor", "detail"].includes(state.view) ? "active" : ""
  }"><span class="icon">▦</span>평가 대시보드</button><button data-action="nav" data-view="members" class="${
    state.view === "members" ? "active" : ""
  }"><span class="icon">♧</span>팀원 관리</button><button data-action="nav" data-view="rubric" class="${
    state.view === "rubric" ? "active" : ""
  }"><span class="icon">▤</span>평가 기준</button><button data-action="nav" data-view="services" class="${
    state.view === "services" ? "active" : ""
  }"><span class="icon">⇄</span>서비스 연결</button></nav><div class="sidebar-foot"><div><span class="live-dot"></span>${
    state.meta.demo ? "DEMO ENVIRONMENT" : "PRIVATE WORKSPACE"
  }</div><div>근거에서 시작하는 공정한 평가</div><button class="subtle small" data-action="logout">로그아웃 ↗</button></div></aside><main class="main"><header class="topbar"><span>워크스페이스 &nbsp; / &nbsp; <strong>${
    labels[state.view]
  }</strong></span><div class="top-right">${
    state.meta.demo ? '<span class="pill demo">DEMO</span>' : ""
  }<span>평가자</span><span class="avatar">R</span></div></header><div class="fade">${content}</div><div class="footer-note">◈ &nbsp; 지정된 근거만 수집 · 산출물 중심 평가 · 조정 이력 보존</div></main></div>`;
}
function loginView() {
  stopPolling();
  $(
    "#app"
  ).innerHTML = `<main class="login-page"><section class="login-art"><div class="brand"><span class="brandmark">T</span>trace.</div><div><div class="eyebrow">EVERY CONTRIBUTION LEAVES A TRACE</div><h1>기여의 흔적을,<br>명확한 평가로.</h1><p>문서와 코드에 담긴 팀원의 기여를 살펴보세요.<br>흩어진 산출물을 연결하고, 근거로 판단합니다.</p></div><span class="tiny muted">PRIVATE TEAM REVIEW WORKSPACE</span></section><section class="login-form"><div class="login-box"><div class="eyebrow" style="margin-bottom:15px">WELCOME TO TRACE</div><h2>평가 워크스페이스</h2><p class="muted small-text">사전 설정된 비밀번호로 안전하게 접속하세요.</p><form id="login-form" class="form-stack"><label>대시보드 비밀번호<input name="password" type="password" autocomplete="current-password" placeholder="비밀번호 입력" required autofocus></label><div id="login-error" class="error" role="alert"></div><button class="primary" type="submit">워크스페이스 접속 &nbsp; →</button></form><div class="footer-note">이 공간의 평가 자료는 인증된 평가자만 확인할 수 있습니다.</div></div></section></main>`;
}
function rubricBlock() {
  return state.meta.rubric.dimensions
    .map(
      (d) =>
        `<div class="rubric-item"><div class="rubric-line"><strong>${esc(
          d.name
        )}</strong><span class="mono">${d.weight}%</span></div><p>${esc(
          d.description
        )}</p><div class="bar"><span style="width:${
          d.weight
        }%"></span></div></div>`
    )
    .join("");
}
function overview() {
  state.view = "overview";
  const evs = state.evaluations;
  const done = evs.filter((e) =>
    ["completed", "finalized"].includes(e.status)
  ).length;
  const running = evs.find((e) => e.status === "running");
  shell(
    `<div class="heading"><div><div class="eyebrow" style="margin-bottom:11px">TEAM CONTRIBUTION</div><h1>기여를 이해하는 새로운 기준</h1><p>팀원의 산출물을 연결하고, 근거에 기반한 평가를 시작하세요.</p></div><button class="primary" data-action="new-evaluation">＋ &nbsp; 새 평가 만들기</button></div>${demoBanner()}<section class="stats"><div class="stat"><span class="glyph">♧</span><div class="label">등록된 팀원</div><div class="value">${
      state.members.filter((m) => m.active).length
    }<span class="unit">명</span></div><div class="hint">복수 계정 통합 관리</div></div><div class="stat"><span class="glyph">▤</span><div class="label">전체 평가</div><div class="value">${
      evs.length
    }<span class="unit">건</span></div><div class="hint">팀의 기여를 기록합니다</div></div><div class="stat"><span class="glyph">◷</span><div class="label">진행 중인 평가</div><div class="value">${
      running ? 1 : 0
    }<span class="unit">건</span></div><div class="hint">${
      running ? "산출물을 분석하고 있습니다" : "새 평가를 시작할 수 있습니다"
    }</div></div><div class="stat"><span class="glyph">✓</span><div class="label">완료된 평가</div><div class="value">${done}<span class="unit">건</span></div><div class="hint">결과 확인 및 세부 조정</div></div></section>${
      running
        ? `<div class="banner info split"><span><i class="spinner"></i><span id="overview-progress">${esc(
            running.title
          )} · ${esc(running.message)} (${
            running.progress
          }%)</span></span><button class="small" data-action="open" data-id="${
            running.id
          }">진행 상황 →</button></div>`
        : ""
    }<div class="grid2"><section class="panel"><header class="panel-head"><h2>평가 목록 <span class="muted small-text">&nbsp; ${
      evs.length
    }</span></h2><span class="eyebrow">EVALUATIONS</span></header>${
      evs.length
        ? `<div class="table-wrap"><table><thead><tr><th>평가명</th><th>참여 규모</th><th>상태</th><th></th></tr></thead><tbody>${evs
            .map(
              (e) =>
                `<tr class="row-link"><td><div class="cell-title">${esc(
                  e.title || "이름 없는 평가"
                )}</div><div class="cell-sub">${e.year} ANNUAL REVIEW ${
                  e.demo ? "· DEMO" : ""
                }</div></td><td>${
                  e.project_count
                }개 프로젝트<div class="cell-sub">${
                  e.member_count
                }명 참여</div></td><td>${badge(
                  e.status
                )}</td><td><button class="small subtle" data-action="open" data-id="${
                  e.id
                }">${
                  e.status === "draft"
                    ? "설정하기"
                    : e.status === "running"
                    ? "진행 확인"
                    : "결과 확인"
                } →</button>${
                  e.status !== "running"
                    ? `<button class="small subtle" data-action="edit-from-list" data-id="${e.id}">편집</button>`
                    : ""
                }</td></tr>`
            )
            .join("")}</tbody></table></div>`
        : `<div class="empty"><div style="font-size:30px">▤</div><h3>첫 평가를 시작해 보세요</h3><p class="small-text">팀원을 등록하고 프로젝트의 근거 링크를 연결하면 됩니다.</p>${
            state.meta.demo && !state.members.length
              ? '<button class="primary" data-action="seed">2026년 데모 불러오기 · 10명 / 6개 프로젝트</button>'
              : '<button data-action="new-evaluation">새 평가 만들기</button>'
          }</div>`
    }</section><section class="panel"><header class="panel-head"><h2>무엇을 평가하나요?</h2><span class="pill">v${esc(
      state.meta.rubric.version
    )}</span></header><div class="panel-body" style="padding-top:6px;padding-bottom:8px">${rubricBlock()}</div><div style="padding:0 24px 22px" class="help">활동량이 아닌 산출물의 질과 문제 해결 과정을 판단합니다.</div></section></div>`
  );
  schedulePoll();
}
function memberIdentityIds(member, key) {
  return Array.isArray(member[key]) ? member[key] : [];
}
function membersView() {
  state.view = "members";
  shell(
    `<div class="heading"><div><div class="eyebrow" style="margin-bottom:11px">PEOPLE & IDENTITIES</div><h1>팀원 관리</h1><p>여러 계정을 한 사람의 기여로 연결합니다. 팀원 ID는 과거 평가에도 유지됩니다.</p></div><button class="primary" data-action="add-member">＋ &nbsp; 팀원 추가</button></div><section class="panel"><header class="panel-head"><h2>팀 디렉터리</h2><span class="muted small-text">${
      state.members.length
    }명 등록</span></header>${
      state.members.length
        ? `<div class="table-wrap"><table><thead><tr><th>팀원</th><th>연차 / 레벨</th><th>GITHUB</th><th>CONFLUENCE</th><th>상태</th><th></th></tr></thead><tbody>${state.members
            .map(
              (m) =>
                `<tr><td><div class="member-card"><span class="avatar">${esc(
                  m.name.slice(0, 1)
                )}</span><div><div class="cell-title">${esc(
                  m.name
                )}</div><div class="cell-sub mono">${esc(
                  m.id
                )}</div></div></div></td><td>${
                  m.career_years
                    ? `${m.career_reference_year}년 기준 ${
                        m.career_years
                      }년차<div class="cell-sub">${careerLevel(
                        m.career_years
                      )}</div>`
                    : '<span class="pill">연차 미등록</span>'
                }</td><td>${memberIdentityIds(m, "github_ids").map(esc).join(", ") || "—"}</td><td>${
                  memberIdentityIds(m, "confluence_ids").map(esc).join(", ") || "—"
                }</td><td><span class="pill ${m.active ? "completed" : ""}">${
                  m.active ? "활성" : "비활성"
                }</span></td><td><button class="small subtle" data-action="edit-member" data-id="${esc(
                  m.id
                )}">편집 ↗</button></td></tr>`
            )
            .join("")}</tbody></table></div>`
        : '<div class="empty">아직 등록된 팀원이 없습니다.</div>'
    }</section><div class="banner info">GitHub 계정과 Confluence accountId / username / userKey를 등록할 수 있습니다. 매핑되지 않은 산출물은 개인 점수에 포함하지 않습니다.</div>`
  );
}
function rubricView() {
  return rubricSettingsView();
}
function careerLevel(years) {
  return years <= 8 ? "CL2" : years <= 16 ? "CL3" : "CL4";
}
function analysisSummary(result) {
  const a = result?.analysis;
  if (!a) return "";
  return `<div class="banner info analysis-summary"><strong>분석 효율</strong><br>근거 본문 ${a.original_chars.toLocaleString()} → ${a.sent_chars.toLocaleString()}자 · ${a.reduction_percent}% 감소<br>연속 변경 통합 ${a.consolidated_groups}개 구간 · LLM 요청 ${a.requests}회 · 기존 판정 재사용 ${a.cache_hits}개 배치<br>${
    a.input_tokens != null || a.output_tokens != null
      ? `API 보고 토큰: 입력 ${a.input_tokens ?? "미제공"} / 출력 ${a.output_tokens ?? "미제공"}`
      : "실제 토큰 수: API 미제공 (위 수치는 문자 수)"
  }<br>원본 근거는 보존합니다. 수집량·절감량은 점수 기준이 아닙니다.</div>`;
}
function renderIdentityList(key) {
  const list = document.querySelector(`[data-identity-list="${key}"]`);
  list.innerHTML =
    state.memberDraft[key]
      .map(
        (id, i) =>
          `<li class="identity-tag"><span>${esc(
            id
          )}</span><button type="button" class="subtle small" data-action="remove-identity" data-key="${key}" data-index="${i}" aria-label="${esc(
            id
          )} 삭제">×</button></li>`
      )
      .join("") || '<li class="help">추가된 ID가 없습니다.</li>';
}
function memberModal(mid) {
  const m = state.members.find((x) => x.id === mid) || {
    id: "",
    name: "",
    github_ids: [],
    confluence_ids: [],
    active: true,
    career_years: 1,
    career_reference_year: new Date().getFullYear(),
  };
  state.memberDraft = {
    github_ids: [...memberIdentityIds(m, "github_ids")],
    confluence_ids: [...memberIdentityIds(m, "confluence_ids")],
  };
  const identityField = (key, label, placeholder) =>
    `<section><label for="${key}-entry">${label}</label><div class="identity-add"><input id="${key}-entry" name="${key}_entry" class="identity-entry" data-key="${key}" placeholder="${placeholder}" autocomplete="off" maxlength="200"><button type="button" data-action="add-identity" data-key="${key}" aria-label="${label} 추가">＋ 추가</button></div><ul class="identity-list" data-identity-list="${key}" aria-label="추가된 ${label}" aria-live="polite"></ul></section>`;
  showModal(
    `<h2>${
      mid ? "팀원 정보 편집" : "새 팀원 추가"
    }</h2><form id="member-form" data-mid="${esc(
      mid || ""
    )}" class="form-stack"><div class="form-grid"><label>팀원 ID<input name="id" value="${esc(
      m.id
    )}" ${
      mid ? "readonly" : ""
    } required placeholder="예: T001"></label><label>이름<input name="name" value="${esc(
      m.name
    )}" required placeholder="팀원 이름"></label><label>연차<input name="career_years" type="number" min="1" max="100" step="1" value="${
      m.career_years ?? ""
    }" required placeholder="예: 9"></label><label>연차 기준 연도<input name="career_reference_year" type="number" min="2000" max="2200" step="1" value="${
      m.career_reference_year ?? new Date().getFullYear()
    }" required></label></div><p class="help" id="career-preview">${
      m.career_years
        ? `${m.career_years}년차 · ${careerLevel(m.career_years)}`
        : "연차를 입력하세요."
    } · 1–8년차 CL2 / 9–16년차 CL3 / 17년차 이상 CL4</p>${identityField(
      "github_ids",
      "GitHub ID",
      "GitHub ID 하나 입력"
    )}${identityField(
      "confluence_ids",
      "Confluence ID",
      "accountId 또는 username 하나 입력"
    )}<label class="checkbox-line"><input name="active" type="checkbox" ${
      m.active ? "checked" : ""
    }>활성 팀원</label><p class="help">ID를 하나씩 입력하고 추가 버튼 또는 Enter를 누르세요. 아래 목록에서 ×로 제거할 수 있습니다. 계정과 연차는 저장 버튼을 눌러 반영합니다.</p><div class="actions"><button type="button" data-action="close-modal">취소</button><button class="primary" type="submit">저장</button></div></form>`
  );
  renderIdentityList("github_ids");
  renderIdentityList("confluence_ids");
}
function newProject(year) {
  return {
    id: uid(),
    name: "",
    start: year + "-01-01",
    end: year + "-12-31",
    weight: 100,
    member_ids: state.members.filter((m) => m.active).map((m) => m.id),
    github_urls: [],
    confluence_urls: [],
  };
}
function sourceList(project, key) {
  return (project[key] || []).map((url, index) =>
    `<li class="identity-tag"><span>${esc(url)}</span><button type="button" class="subtle small" data-action="remove-source" data-key="${key}" data-index="${index}" aria-label="${esc(url)} 삭제">✕</button></li>`
  ).join("") || '<li class="help">추가된 링크가 없습니다.</li>';
}
function sourceControl(project, key, label, placeholder, help) {
  const entry = state.editor.sourceEntries?.[project.id]?.[key] || "";
  const id = esc(`source-${project.id}-${key}`);
  return `<section class="source-control"><label for="${id}">${label}</label><div class="identity-add"><input id="${id}" type="url" data-source-entry="${key}" value="${esc(entry)}" placeholder="${esc(placeholder)}" autocomplete="off" maxlength="2048"><button type="button" data-action="add-source" data-key="${key}" aria-label="${label} 추가">＋ 추가</button></div><ul class="identity-list source-list" data-source-list="${key}" aria-label="추가된 ${label}" aria-live="polite">${sourceList(project, key)}</ul><p class="help">${help}</p></section>`;
}
function editorView() {
  state.view = "editor";
  const e = state.editor;
  const total = e.projects.reduce((a, p) => a + Number(p.weight), 0);
  e.rubric ||= structuredClone(state.meta.rubric);
  e.editorWeights ||= structuredClone(
    e.level_policy?.weights || state.meta.level_policy.weights
  );
  const isCorrection = e.id && e.status !== "draft";
  shell(
    `<div class="heading"><div><div class="eyebrow" style="margin-bottom:11px">CONFIGURE REVIEW</div><h1>${
      e.id ? "평가 설정" : "새 평가 만들기"
    }</h1><p>이름·참여자·근거 링크와 가중치를 입력 중이어도 초안으로 저장할 수 있습니다. 평가 시작 전에 설정을 완성하세요.</p></div><button data-action="nav" data-view="overview">← 목록으로</button></div><section class="panel"><div class="panel-body form-grid"><label>평가 이름<input id="eval-title" value="${esc(
      e.title
    )}" placeholder="예: 2026년 팀원 기여도 평가" required></label><label>평가 연도<input id="eval-year" type="number" min="2000" max="2200" value="${
      e.year
    }"></label></div></section><div class="split"><h2>프로젝트 <span class="muted small-text">${
      e.projects.length
    }개</span></h2><div class="actions"><button class="small" data-action="add-member">＋ 팀원 등록</button><button class="small" data-action="equalize">가중치 균등 분배</button><button class="small" data-action="add-project">＋ 프로젝트 추가</button></div></div>${e.projects
      .map(
        (p, i) =>
          `<section class="project-card" data-project="${
            p.id
          }"><div class="project-head"><span class="project-number mono">${String(
            i + 1
          ).padStart(
            2,
            "0"
          )}</span><input aria-label="프로젝트 이름" data-field="name" value="${esc(
            p.name
          )}" placeholder="프로젝트 이름"><button class="small subtle danger" data-action="remove-project" data-id="${
            p.id
          }" aria-label="프로젝트 삭제">✕</button></div><div class="project-body form-stack"><div class="form-grid"><label>평가 시작일<input data-field="start" type="date" value="${
            p.start || ""
          }"></label><label>평가 종료일<input data-field="end" type="date" value="${
            p.end || ""
          }"></label></div><div><div class="split" style="margin-bottom:10px"><label>참여 팀원 · ${
            p.member_ids.length
          }명</label><button class="subtle small" data-action="all-members" data-id="${
            p.id
          }">전체 선택 / 해제</button></div><div class="chips">${
            state.members
              .filter((m) => m.active || p.member_ids.includes(m.id))
              .map(
                (m) =>
                  `<button class="chip ${
                    p.member_ids.includes(m.id) ? "selected" : ""
                  }" aria-pressed="${p.member_ids.includes(
                    m.id
                  )}" data-action="toggle-member" data-pid="${
                    p.id
                  }" data-mid="${esc(m.id)}">${
                    p.member_ids.includes(m.id) ? "✓ " : ""
                  }${esc(m.name)}</button>`
              )
              .join("") || '<p class="help">먼저 팀원을 등록하세요.</p>'
          }</div></div><div class="form-grid">${sourceControl(
            p, "github_urls", "GitHub repo 또는 org URL",
            state.meta.services.github.url + "/org/repo",
            "추가한 모든 저장소를 분석합니다. 조직은 실행 시 소유 저장소 목록을 고정합니다."
          )}${sourceControl(
            p, "confluence_urls", "Confluence 루트 페이지 URL",
            "루트 페이지 주소를 붙여넣으세요",
            "추가한 모든 루트와 하위 페이지를 분석합니다. 본문 링크는 따라가지 않습니다."
          )}</div><label>프로젝트 가중치<div class="weight"><input aria-label="프로젝트 가중치 슬라이더" data-weight-range="${
            p.id
          }" type="range" min="1" max="100" step="1" value="${
            p.weight
          }"><input aria-label="프로젝트 가중치 값" data-field="weight" type="number" min="0.01" max="100" step="0.01" value="${
            p.weight
          }"></div></label></div></section>`
      )
      .join(
        ""
      )}<section class="panel"><header class="panel-head"><h2>이 평가의 기준과 보정</h2><button class="small" data-action="load-current-rubric">현재 기본 기준 불러오기</button></header><div class="panel-body form-stack">${rubricControls(
      e.rubric,
      e.editorWeights,
      "evaluation"
    )}<label class="checkbox-line"><input id="apply-level-policy" type="checkbox" ${
      e.level_policy ? "disabled" : ""
    } ${
      e.applyLevel ?? (!!e.level_policy || !isCorrection) ? "checked" : ""
    }>연차별 CL 계수 적용</label>${
      isCorrection
        ? `<label class="checkbox-line"><input id="refresh-members" type="checkbox" ${
            e.refresh_members ? "checked" : ""
          }>최신 팀원 정보 반영 (계정·이름·연차)</label><label>수정 사유<textarea id="edit-reason" minlength="3" required placeholder="변경 이유를 작성하세요">${esc(
            e.reason || ""
          )}</textarea></label><p class="help">변경 전 구성과 결과는 이력에 보존됩니다. 추가된 참여자와 근거·판정 내용이 달라진 항목만 재분석합니다. 확정된 평가는 저장 시 검토 상태로 전환됩니다.</p>`
        : ""
    }</div></section><div class="weight-summary"><div><strong>전체 가중치 &nbsp; <span id="weight-total" class="${
      Math.abs(total - 100) < 0.001 ? "valid" : "invalid"
    }">${total.toFixed(
      2
    )} / 100</span></strong><div style="font-size:11px;color:#9db198;margin-top:5px">설정 변경은 저장 후 반영됩니다. 필요한 항목은 재분석할 수 있습니다.</div></div><div class="actions">${
      e.id && e.status === "draft"
        ? '<button class="subtle" data-action="delete-draft">초안 삭제</button>'
        : ""
    }<button data-action="save-evaluation">${
      isCorrection ? "변경 사항 저장" : "초안 저장"
    } &nbsp; →</button></div></div>`
  );
}
function captureEditor() {
  if (!state.editor || state.view !== "editor") return;
  const edits = readRubricControls(document);
  state.editor.rubric = edits.rubric;
  state.editor.editorWeights = edits.level_weights;
  state.editor.applyLevel = $("#apply-level-policy").checked;
  state.editor.refresh_members = $("#refresh-members")?.checked || false;
  state.editor.reason = $("#edit-reason")?.value || "";
  state.editor.title = $("#eval-title").value;
  state.editor.year = Number($("#eval-year").value);
  document.querySelectorAll("[data-project]").forEach((el) => {
    const p = state.editor.projects.find((p) => p.id === el.dataset.project);
    state.editor.sourceEntries ||= {};
    state.editor.sourceEntries[p.id] = Object.fromEntries(
      [...el.querySelectorAll("[data-source-entry]")].map(input => [input.dataset.sourceEntry, input.value])
    );
    el.querySelectorAll("[data-field]").forEach((input) => {
      const key = input.dataset.field;
      p[key] = key === "weight" ? Number(input.value) : input.value;
    });
  });
}
function updateWeight() {
  captureEditor();
  const total = state.editor.projects.reduce((a, p) => a + p.weight, 0);
  $("#weight-total").textContent = total.toFixed(2) + " / 100";
  $("#weight-total").className =
    Math.abs(total - 100) < 0.001 ? "valid" : "invalid";
}
async function openEvaluation(id) {
  stopPolling();
  state.evaluation = await api("/evaluations/" + id);
  state.view = "detail";
  state.selectedMember = null;
  state.selectedProject = null;
  detailView();
}
function detailView() {
  const e = state.evaluation;
  state.view = "detail";
  const completed = ["completed", "finalized"].includes(e.status);
  const agg = e.aggregate || [];
  const selected =
    agg.find((m) => m.member_id === state.selectedMember) || agg[0];
  if (selected) state.selectedMember = selected.member_id;
  let body = "";
  if (completed && selected) {
    const projects = e.projects.filter((p) =>
      p.member_ids.includes(selected.member_id)
    );
    const project =
      projects.find((p) => p.id === state.selectedProject) || projects[0];
    state.selectedProject = project.id;
    const result = e.results.find(
      (r) => r.project_id === project.id && r.member_id === selected.member_id
    );
    const pscore = selected.projects.find((p) => p.project_id === project.id);
    body = `<section class="stats"><div class="stat"><div class="label">평가 팀원</div><div class="value">${
      agg.length
    }<span class="unit">명</span></div></div><div class="stat"><div class="label">프로젝트</div><div class="value">${
      e.projects.length
    }<span class="unit">개</span></div></div><div class="stat"><div class="label">수집된 근거</div><div class="value">${
      Object.values(e.evidence || {}).flat().length
    }<span class="unit">건</span></div><div class="hint">수량은 점수에 반영하지 않음</div></div><div class="stat"><div class="label">평가 기준</div><div class="value">v${esc(
      e.rubric.version
    )}</div><div class="hint">${esc(
      e.model
    )}</div></div></section><div class="result-layout"><aside class="panel member-list">${agg
      .map(
        (m) =>
          `<button class="member-select ${
            selected.member_id === m.member_id ? "active" : ""
          }" data-action="select-result-member" data-id="${esc(
            m.member_id
          )}"><span><strong>${esc(m.name)}</strong><div class="cell-sub">${
            m.coverage < 100 ? "잠정 · " : ""
          }${m.level ? m.level + " · " : ""}충족률 ${
            m.coverage
          }%</div></span><span class="score-bubble">${score(
            m.score
          )}</span></button>`
      )
      .join(
        ""
      )}</aside><section><div class="panel"><div class="panel-body"><div class="detail-summary"><div><div class="eyebrow" style="margin-bottom:10px">INDIVIDUAL CONTRIBUTION</div><h2>${esc(
      selected.name
    )} <span class="muted small-text">${esc(
      selected.member_id
    )}</span></h2><p class="help" style="margin-top:8px">${
      selected.projects.length
    }개 프로젝트 참여 · 평가 가능한 가중치 ${selected.assessed_weight} / ${
      selected.participating_weight
    }<br>${
      selected.coverage < 100
        ? "판정 보류 항목이 있어 잠정 종합점수입니다."
        : "모든 참여 프로젝트가 평가되었습니다."
    }</p></div><div style="text-align:right"><div class="help">종합점수</div><div class="score">${score(
      selected.score
    )} <small>/ 100</small></div></div></div><p class="help" style="margin-top:15px">${
      selected.level_applied
        ? `${e.year}년 기준 ${selected.career_years}년차 · ${
            selected.level
          } · 보정 전 ${score(
            selected.raw_score
          )} × 레벨 계수 ${selected.level_weight.toFixed(2)} = 종합 ${score(
            selected.score
          )}`
        : "레벨 보정 도입 전 평가입니다. 기존 점수를 유지합니다."
    }<br>보정 전 점수 = 평가 가능한 참여 프로젝트의 점수 × 프로젝트 가중치 / 해당 가중치 합. 미참여 프로젝트는 제외합니다.</p></div></div><nav class="project-tabs" aria-label="프로젝트 결과">${projects
      .map(
        (p) =>
          `<button data-action="select-result-project" data-id="${
            p.id
          }" class="${p.id === project.id ? "active" : ""}">${esc(
            p.name
          )} &nbsp; ${p.weight}%</button>`
      )
      .join(
        ""
      )}</nav><div class="panel"><header class="panel-head"><div><h2>${esc(
      project.name
    )}</h2><p class="help" style="margin-top:6px">${project.start} — ${
      project.end
    } · 프로젝트 가중치 ${project.weight}%</p></div><div class="score">${score(
      pscore?.score
    )} <small>/ 100</small></div></header><div class="panel-body">${
      result
        ? `<button class="small subtle" data-action="review-text" ${
            result.analysis_pending ? "disabled" : ""
          }>평가 의견 편집 ↗</button><p class="small-text" style="white-space:pre-line">${esc(
            result.adjusted_summary ?? result.summary
          )}</p>${analysisSummary(result)}${Object.entries(result.dimensions)
            .map(([key, d]) => {
              const adjusted = Object.hasOwn(d, "adjusted_score");
              return `<div class="dimension"><div class="dimension-head"><div><strong>${esc(
                e.rubric.dimensions.find((d) => d.id === key)?.name ||
                  dimensionNames[key]
              )}</strong> <span class="muted tiny">${
                e.rubric.dimensions.find((x) => x.id === key).weight
              }%</span>${
                adjusted ? '<span class="adjusted">평가자 조정</span>' : ""
              }</div><div class="actions"><strong>${score(
                adjusted ? d.adjusted_score : d.score
              )}</strong>${
                ["completed", "finalized"].includes(e.status) &&
                !result.analysis_pending
                  ? `<button class="small subtle" data-action="adjust" data-dim="${key}">조정 ↗</button>`
                  : ""
              }</div></div><p>${esc(
                d.reason
              )}</p><div class="help" style="margin-top:8px">LLM 원점수 ${score(
                d.score
              )} · 신뢰도 ${Math.round(d.confidence * 100)}%</div>${
                adjusted
                  ? `<div class="banner" style="margin:12px 0">조정 이유: ${esc(
                      d.adjustment_reason
                    )}${
                      d.reviewer_comment
                        ? "<br>평가 의견: " + esc(d.reviewer_comment)
                        : ""
                    }</div>`
                  : ""
              }${d.citations
                .map(
                  (c) =>
                    `<div class="citation">${esc(
                      c.quote
                    )}<br><button class="subtle" data-action="evidence" data-eid="${esc(
                      c.evidence_id
                    )}">근거 원문 확인 &nbsp; ↗</button></div>`
                )
                .join("")}</div>`;
            })
            .join(
              ""
            )}<div class="banner info" style="margin-top:20px;margin-bottom:0"><strong>판단의 한계</strong><br>${esc(
            result.adjusted_limitations ?? result.limitations
          )}</div>`
        : "<p>평가 결과가 없습니다.</p>"
    }</div></div><div class="panel"><div class="panel-body"><details><summary>이 프로젝트의 수집 범위와 전체 근거 확인</summary><p class="help" style="margin:12px 0">GitHub 기본 브랜치 · UTC 날짜 기준. 미귀속 자료도 여기서 확인할 수 있습니다.</p><pre class="help" style="white-space:pre-wrap">${esc(
      JSON.stringify(e.scopes?.[project.id], null, 2)
    )}</pre>${(e.evidence?.[project.id] || [])
      .map(
        (v) =>
          `<div class="split" style="padding:8px 0;border-top:1px solid var(--border)"><span class="help">${esc(
            v.source
          )} · ${esc(v.title)} · ${esc(
            v.member_id || "미귀속"
          )}</span><button class="small subtle" data-action="evidence" data-eid="${esc(
            v.id
          )}">보기</button></div>`
      )
      .join("")}</details></div></div></section></div>`;
  } else {
    body = `<section class="panel"><div class="panel-body"><div class="split"><h2>${
      e.status === "running"
        ? '<i class="spinner"></i>근거를 분석하고 있습니다'
        : e.status === "draft"
        ? "설정이 준비되었습니다"
        : esc(statusNames[e.status])
    }</h2>${badge(
      e.status
    )}</div><p id="progress-message" class="muted small-text" style="margin-top:15px">${esc(
      e.error || e.message || "프로젝트 설정을 확인한 후 평가를 시작하세요."
    )}</p>${
      e.status === "running"
        ? `<div class="bar progress-main" role="progressbar" aria-valuenow="${
            e.progress
          }" aria-valuemin="0" aria-valuemax="100"><span style="width:${
            e.progress
          }%"></span></div><div class="split"><span class="help">다른 평가를 동시에 실행할 수 없습니다. 이 화면을 닫아도 작업은 계속됩니다.</span><strong id="progress-value" class="mono">${
            e.progress
          }%</strong></div><button class="small danger" style="margin-top:22px" data-action="cancel">${
            e.cancel_requested ? "취소 요청됨" : "실행 취소"
          }</button>`
        : ""
    }</div></section><section class="panel"><div class="panel-head"><h2>프로젝트 구성</h2><span class="pill">총 가중치 ${e.projects.reduce((sum, p) => sum + p.weight, 0)}%</span></div><div class="panel-body project-weight-grid">${e.projects
      .map(
        (p, index) =>
          `<div class="project-weight-item"><div class="split"><strong>${esc(
            p.name || `프로젝트 ${index + 1} (이름 미입력)`
          )}</strong><span class="mono">${
            p.weight
          }%</span></div><p class="help" style="margin:12px 0">${p.start || "시작일 미입력"} — ${
            p.end || "종료일 미입력"
          }<br>${p.member_ids.length}명 참여 · GitHub ${
            p.github_urls.length
          } / Confluence ${
            p.confluence_urls.length
          }</p><div class="bar"><span style="width:${
            p.weight
          }%"></span></div></div>`
      )
      .join("")}</div></section>`;
  }
  shell(
    `<div class="heading"><div><div class="eyebrow" style="margin-bottom:11px">${
      e.year
    } REVIEW &nbsp; / &nbsp; ${esc(statusNames[e.status])}</div><h1>${esc(
      e.title || "이름 없는 평가"
    )}</h1><p>${
      completed
        ? "산출물과 판단 근거를 검토하고, 필요한 항목을 세부 조정하세요."
        : "정해진 범위 안에서 문서와 코드를 수집하고 개인 기여를 평가합니다."
    }</p></div><div class="actions"><button class="small" data-action="nav" data-view="overview">← 목록</button>${
      e.status !== "running"
        ? '<button class="small" data-action="edit-evaluation">평가 편집</button>'
        : ""
    }${
      ["draft", "failed", "cancelled"].includes(e.status) || e.needs_analysis
        ? `<button class="primary" data-action="start" ${
            busy() ? "disabled" : ""
          }>${
            e.status === "draft"
              ? "평가 시작"
              : e.needs_analysis
              ? "변경 항목 재분석"
              : "다시 시도"
          } →</button>`
        : ""
    }${
      completed
        ? `<button class="small" data-action="reanalyze-all">전체 다시 분석</button><button class="small" data-action="audit">변경 이력</button><button class="small" data-action="export">내보내기</button><button class="primary small" data-action="${
            e.status === "finalized" ? "reopen" : "finalize"
          }">${e.status === "finalized" ? "확정 해제" : "최종 확정"}</button>`
        : ""
    }${
      e.status !== "running"
        ? '<button class="small" data-action="clone">복제</button>'
        : ""
    }</div></div>${e.demo ? demoBanner() : ""}${
      e.status === "draft" && e.draft_issues?.length
        ? `<div class="banner" role="status"><strong>초안이 저장되었습니다. 평가 시작 전에 다음 항목을 확인하세요.</strong><ul>${e.draft_issues.map((message) => `<li>${esc(message)}</li>`).join("")}</ul><button class="small" data-action="edit-evaluation">설정 보완하기</button></div>`
        : ""
    }${
      `<div id="evaluation-warnings">${warningBlock(e.warnings || [])}</div>`
    }${
      e.status === "finalized"
        ? `<div class="banner info">확정된 평가입니다. 편집 내용을 저장하면 검토 상태로 전환됩니다. ${esc(
            e.finalization_reason || ""
          )}</div>`
        : ""
    }${
      e.needs_analysis
        ? '<div class="banner">수정된 항목에 재분석이 필요합니다. 유지 가능한 기존 결과만 종합점수에 반영되며, 재분석 완료 전에는 확정할 수 없습니다.</div>'
        : ""
    }${body}`
  );
  schedulePoll();
}
function showModal(html) {
  const dialog = $("#modal");
  dialog.innerHTML = html;
  if (!dialog.open) dialog.showModal();
}
function closeModal() {
  $("#modal").close();
}
function adjustmentModal(key) {
  const e = state.evaluation;
  const r = e.results.find(
    (r) =>
      r.member_id === state.selectedMember &&
      r.project_id === state.selectedProject
  );
  const d = r.dimensions[key];
  const val = d.adjusted_score ?? d.score ?? 50;
  showModal(
    `<h2>${esc(
      e.rubric.dimensions.find((d) => d.id === key)?.name || dimensionNames[key]
    )} 조정</h2><p class="help" style="margin-bottom:20px">LLM 원점수 ${score(
      d.score
    )}는 보존됩니다. 변경 사항은 프로젝트 및 종합점수에 즉시 반영됩니다.</p><form id="adjust-form" data-dim="${key}" class="form-stack"><label>평가자 점수<div class="weight"><input name="score_range" type="range" min="0" max="100" value="${val}"><input name="score" aria-label="평가자 점수" type="number" min="0" max="100" step="0.1" value="${val}" required></div></label><label class="checkbox-line"><input name="unassessed" type="checkbox">판정 보류로 변경 (점수 없음)</label><label>조정 사유 · 필수<textarea name="reason" minlength="3" required placeholder="근거를 검토한 뒤 조정이 필요한 이유를 작성하세요"></textarea></label><label>평가 의견 · 선택<textarea name="comment" placeholder="추가 평가 내용을 남길 수 있습니다">${esc(
      d.reviewer_comment || ""
    )}</textarea></label><div class="actions">${
      Object.hasOwn(d, "adjusted_score")
        ? '<button type="submit" name="reset" value="1">LLM 원점수로 복원</button>'
        : ""
    }<button type="button" data-action="close-modal">취소</button><button class="primary" type="submit">조정 반영</button></div></form>`
  );
}
function warningBlock(warnings) {
  return warnings.length ? `<div class="banner"><details><summary>수집 및 귀속 경고 ${warnings.length}건 · 결과 검토 시 확인하세요</summary><ul>${warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul></details></div>` : "";
}
function updateRunningDetail(previous) {
  const e = state.evaluation;
  const setText = (selector, text) => {
    const node = $(selector);
    if (node.textContent !== String(text)) node.textContent = text;
  };
  setText("#progress-message", e.error || e.message || "근거를 분석하고 있습니다");
  setText("#progress-value", `${e.progress}%`);
  const bar = $('[role="progressbar"]');
  if (bar.getAttribute("aria-valuenow") !== String(e.progress)) {
    bar.setAttribute("aria-valuenow", e.progress);
    $("span", bar).style.width = `${e.progress}%`;
  }
  setText('[data-action="cancel"]', e.cancel_requested ? "취소 요청됨" : "실행 취소");
  if (JSON.stringify(previous.warnings) !== JSON.stringify(e.warnings)) {
    const container = $("#evaluation-warnings");
    if ($("details", container) && e.warnings?.length) {
      $("summary", container).textContent = `수집 및 귀속 경고 ${e.warnings.length}건 · 결과 검토 시 확인하세요`;
      $("ul", container).innerHTML = e.warnings.map(w => `<li>${esc(w)}</li>`).join("");
    } else {
      container.innerHTML = warningBlock(e.warnings || []);
    }
  }
}
function overviewStructure(members, evaluations) {
  return JSON.stringify([members.filter(m => m.active).length, evaluations.map(e =>
    [e.id, e.title, e.year, e.demo, e.project_count, e.member_count, e.status])]);
}
function stopPolling() {
  clearTimeout(state.timer);
  state.pollEpoch++;
}
function schedulePoll() {
  stopPolling();
  const view = state.view;
  if (!["detail", "overview"].includes(view)) return;
  const active = state.evaluations.some(e => e.status === "running") ||
    (view === "detail" && state.evaluation?.status === "running");
  if (!active) return;
  const epoch = state.pollEpoch;
  const id = view === "detail" ? state.evaluation?.id : null;
  const current = () => epoch === state.pollEpoch && view === state.view &&
    (view !== "detail" || id === state.evaluation?.id);
  state.timer = setTimeout(async () => {
    try {
      const [members, evaluations, evaluation] = await Promise.all([
        api("/members"), api("/evaluations"),
        id ? api("/evaluations/" + id) : Promise.resolve(null),
      ]);
      // A response from a screen we left must never replace the current screen.
      if (!current()) return;
      const previous = state.evaluation;
      const changed = overviewStructure(state.members, state.evaluations) !== overviewStructure(members, evaluations);
      const wasBusy = busy();
      state.members = members;
      state.evaluations = evaluations;
      if (view === "detail") {
        state.evaluation = evaluation;
        if (previous.status === "running" && evaluation.status === "running") {
          updateRunningDetail(previous);
        } else if (JSON.stringify(previous) !== JSON.stringify(evaluation) || wasBusy !== busy()) {
          detailView();
          return;
        }
      } else if (changed) {
        overview();
        return;
      } else {
        const running = evaluations.find(e => e.status === "running");
        const node = $("#overview-progress");
        if (running && node) {
          const text = `${running.title} · ${running.message} (${running.progress}%)`;
          if (node.textContent !== text) node.textContent = text;
        }
      }
      schedulePoll();
    } catch (e) {
      if (current()) {
        toast(e.message);
        schedulePoll();
      }
    }
  }, 1800);
}
async function navigate(view) {
  stopPolling();
  await refresh();
  if (view === "overview") overview();
  if (view === "members") membersView();
  if (view === "rubric") await rubricView();
  if (view === "services") await servicesView();
}
async function handleAction(el) {
  const action = el.dataset.action;
  if (action === "add-identity") {
    const key = el.dataset.key;
    const input = document.querySelector(`[name="${key}_entry"]`);
    const value = input.value.trim().toLowerCase();
    if (!value || /[,\s]/.test(value))
      return toast("ID를 공백이나 쉼표 없이 하나만 입력하세요.");
    if (state.memberDraft[key].includes(value))
      return toast("이미 추가된 ID입니다.");
    if (state.memberDraft[key].length >= 20)
      return toast("ID는 서비스별 최대 20개까지 추가할 수 있습니다.");
    state.memberDraft[key].push(value);
    input.value = "";
    renderIdentityList(key);
    input.focus();
    return;
  }
  if (action === "remove-identity") {
    state.memberDraft[el.dataset.key].splice(Number(el.dataset.index), 1);
    renderIdentityList(el.dataset.key);
    return;
  }
  captureEditor();
  if (["add-source", "remove-source"].includes(action)) {
    const card = el.closest("[data-project]");
    const project = state.editor.projects.find(p => p.id === card.dataset.project);
    const key = el.dataset.key;
    const input = $(`[data-source-entry="${key}"]`, card);
    if (action === "add-source") {
      const value = input.value.trim();
      let parsed;
      try { parsed = new URL(value); } catch (_) { /* Show the same validation below. */ }
      if (!parsed || !["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || /\s/.test(value))
        return toast("http 또는 https URL을 하나만 입력하세요.");
      if (project[key].includes(value)) return toast("이미 추가된 링크입니다.");
      if (project[key].length >= 30) return toast("링크는 프로젝트의 서비스별 최대 30개까지 추가할 수 있습니다.");
      project[key].push(value);
      input.value = "";
      state.editor.sourceEntries[project.id][key] = "";
    } else {
      project[key].splice(Number(el.dataset.index), 1);
    }
    $(`[data-source-list="${key}"]`, card).innerHTML = sourceList(project, key);
    input.focus();
    return;
  }
  if (action === "close-modal") return closeModal();
  if (action === "nav") return navigate(el.dataset.view);
  if (action === "logout") {
    await api("/logout", "POST", {});
    return loginView();
  }
  if (action === "seed") {
    el.disabled = true;
    state.evaluation = await api("/demo/seed", "POST", {});
    await refresh();
    toast("팀원 10명과 프로젝트 6개를 준비했습니다.");
    return openEvaluation(state.evaluation.id);
  }
  if (action === "new-evaluation") {
    const year = new Date().getFullYear();
    state.editor = {
      title: year + "년 팀원 기여도 평가",
      year,
      revision: 0,
      projects: [newProject(year)],
    };
    stopPolling();
    return editorView();
  }
  if (action === "add-member") return memberModal();
  if (action === "edit-member") return memberModal(el.dataset.id);
  if (action === "open") return openEvaluation(el.dataset.id);
  if (action === "review-text") return reviewTextModal();
  if (action === "reanalyze-all") return reanalyzeModal();
  if (action === "load-current-rubric") {
    state.meta = await api("/meta");
    state.editor.rubric = structuredClone(state.meta.rubric);
    state.editor.editorWeights = structuredClone(
      state.meta.level_policy.weights
    );
    state.editor.applyLevel = true;
    return editorView();
  }
  if (action === "edit-from-list") {
    state.editor = await api("/evaluations/" + el.dataset.id);
    stopPolling();
    return editorView();
  }
  if (action === "edit-evaluation") {
    state.editor = JSON.parse(JSON.stringify(state.evaluation));
    stopPolling();
    return editorView();
  }
  if (action === "add-project") {
    const p = newProject(state.editor.year);
    p.weight = 10;
    state.editor.projects.push(p);
    return editorView();
  }
  if (action === "remove-project") {
    if (state.editor.projects.length === 1)
      return toast("프로젝트는 하나 이상 필요합니다.");
    state.editor.projects = state.editor.projects.filter(
      (p) => p.id !== el.dataset.id
    );
    return editorView();
  }
  if (action === "equalize") {
    const n = state.editor.projects.length;
    const base = Math.floor(10000 / n) / 100;
    state.editor.projects.forEach(
      (p, i) =>
        (p.weight =
          i === n - 1 ? Math.round((100 - base * (n - 1)) * 100) / 100 : base)
    );
    return editorView();
  }
  if (action === "toggle-member") {
    const p = state.editor.projects.find((p) => p.id === el.dataset.pid);
    p.member_ids = p.member_ids.includes(el.dataset.mid)
      ? p.member_ids.filter((id) => id !== el.dataset.mid)
      : [...p.member_ids, el.dataset.mid];
    return editorView();
  }
  if (action === "all-members") {
    const p = state.editor.projects.find((p) => p.id === el.dataset.id);
    const all = state.members.filter((m) => m.active).map((m) => m.id);
    p.member_ids = p.member_ids.length === all.length ? [] : all;
    return editorView();
  }
  if (action === "save-evaluation") {
    const pending = [...document.querySelectorAll("[data-source-entry]")].find(input => input.value.trim());
    if (pending) {
      pending.focus();
      return toast("입력 중인 링크의 추가 버튼을 누른 뒤 저장하세요.");
    }
    const e = state.editor;
    const body = {
      title: e.title,
      year: e.year,
      projects: e.projects,
      revision: e.revision,
      reason: e.reason || "",
      refresh_members: !!e.refresh_members,
      rubric: e.rubric,
      level_weights: e.applyLevel ? e.editorWeights : null,
    };
    el.disabled = true;
    try {
      const saved = await api(
        "/evaluations" + (e.id ? "/" + e.id : ""),
        e.id ? "PUT" : "POST",
        body
      );
      await refresh();
      toast("평가 설정을 저장했습니다.");
      return openEvaluation(saved.id);
    } finally {
      el.disabled = false;
    }
  }
  if (action === "delete-draft") {
    if (!confirm("이 초안을 삭제하시겠습니까?")) return;
    await api("/evaluations/" + state.editor.id, "DELETE", {
      revision: state.editor.revision,
    });
    return navigate("overview");
  }
  if (action === "start") {
    el.disabled = true;
    try {
      state.evaluation = await api(
        "/evaluations/" + state.evaluation.id + "/start",
        "POST",
        { revision: state.evaluation.revision }
      );
      await refresh();
      detailView();
    } finally {
      el.disabled = false;
    }
    return;
  }
  if (action === "cancel") {
    await api("/evaluations/" + state.evaluation.id + "/cancel", "POST", {});
    state.evaluation = await api("/evaluations/" + state.evaluation.id);
    return detailView();
  }
  if (action === "clone") {
    const e = await api(
      "/evaluations/" + state.evaluation.id + "/clone",
      "POST",
      {}
    );
    state.editor = e;
    toast("새 초안으로 복제했습니다.");
    return editorView();
  }
  if (action === "select-result-member") {
    state.selectedMember = el.dataset.id;
    state.selectedProject = null;
    return detailView();
  }
  if (action === "select-result-project") {
    state.selectedProject = el.dataset.id;
    return detailView();
  }
  if (action === "adjust") return adjustmentModal(el.dataset.dim);
  if (action === "evidence") {
    const e = Object.values(state.evaluation.evidence)
      .flat()
      .find((x) => x.id === el.dataset.eid);
    return showModal(
      `<h2>근거 원문</h2><p>${esc(e.title)}</p><p class="help">${esc(
        e.source
      )} · ${esc(e.date)} · ${esc(e.member_id || "미귀속")} ${
        e.truncated ? "· 일부 내용 생략" : ""
      }</p><pre>${esc(
        e.content
      )}</pre>${e.metadata?.kind === "github_consolidated" ? `<section><h3>통합 전 커밋 근거</h3><p class="help">원본 변경 이력과 중간 작업을 확인할 수 있습니다.</p>${Object.keys(e.metadata.source_hashes).map((id, i) => `<button class="small subtle" data-action="evidence" data-eid="${esc(id)}">커밋 근거 ${i + 1} ↗</button>`).join("")}</section>` : ""}<p class="help mono" style="overflow-wrap:anywhere">원문 SHA-256: ${esc(
        e.sha256
      )}</p><div class="actions"><a href="${esc(
        e.url
      )}" target="_blank" rel="noopener noreferrer">원본 서비스에서 열기 ↗</a><button data-action="close-modal">닫기</button></div>`
    );
  }
  if (action === "export")
    return showModal(
      `<h2>평가 결과 내보내기</h2><p class="help">JSON에는 원문 근거, 원점수, 조정 내용과 감사 이력이 포함됩니다. CSV에는 개인별·프로젝트별 최종 점수가 포함됩니다.</p><div class="actions"><a href="/api/evaluations/${state.evaluation.id}/export?format=json" download>JSON 다운로드 ↗</a><a href="/api/evaluations/${state.evaluation.id}/export?format=csv" download>CSV 다운로드 ↗</a><button data-action="close-modal">닫기</button></div>`
    );
  if (action === "audit") {
    const rows = await api("/evaluations/" + state.evaluation.id + "/audit");
    return showModal(
      `<h2>변경 이력</h2>${rows
        .map(
          (r) =>
            `<div class="audit-entry"><div class="split"><strong>${esc(
              r.action
            )}</strong><span class="help">${esc(
              new Date(r.at).toLocaleString("ko-KR")
            )}</span></div><pre>${esc(
              JSON.stringify(r.data, null, 2)
            )}</pre></div>`
        )
        .join(
          ""
        )}<div class="actions"><button data-action="close-modal">닫기</button></div>`
    );
  }
  if (action === "finalize" || action === "reopen")
    return showModal(
      `<h2>${
        action === "finalize" ? "최종 평가 확정" : "확정 해제"
      }</h2><form id="decision-form" data-decision="${action}" class="form-stack"><p class="help">${
        action === "finalize"
          ? "검토 결과를 확정합니다. 이후 편집하면 검토 상태로 돌아갑니다. 경고·판정 보류가 있으면 검토 내용을 사유로 남겨주세요."
          : "사유를 남기면 다시 세부 점수를 조정할 수 있습니다."
      }</p><label>검토 사유<textarea name="reason" placeholder="검토 내용 또는 확정 해제 사유" ${
        action === "reopen" ? 'required minlength="3"' : ""
      }></textarea></label><div class="actions"><button type="button" data-action="close-modal">취소</button><button class="primary" type="submit">${
        action === "finalize" ? "평가 확정" : "확정 해제"
      }</button></div></form>`
    );
}
document.addEventListener("click", async (event) => {
  const el = event.target.closest("[data-action]");
  if (!el || el.disabled) return;
  try {
    await handleAction(el);
  } catch (e) {
    toast(e.message);
    el.disabled = false;
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches("[data-source-entry]")) {
    event.preventDefault();
    $(`[data-action="add-source"][data-key="${event.target.dataset.sourceEntry}"]`, event.target.closest("[data-project]")).click();
  }
  if (event.key === "Enter" && event.target.matches(".identity-entry")) {
    event.preventDefault();
    document
      .querySelector(
        `[data-action="add-identity"][data-key="${event.target.dataset.key}"]`
      )
      .click();
  }
});
document.addEventListener("input", (event) => {
  if (event.target.name === "career_years") {
    const years = Number(event.target.value);
    $("#career-preview").textContent =
      years >= 1
        ? `${years}년차 · ${careerLevel(
            years
          )} · 1–8년차 CL2 / 9–16년차 CL3 / 17년차 이상 CL4`
        : "연차를 입력하세요.";
  }
  const el = event.target;
  if (el.dataset.weightRange) {
    const card = el.closest("[data-project]");
    $('[data-field="weight"]', card).value = el.value;
    updateWeight();
  } else if (el.dataset.field === "weight") {
    const card = el.closest("[data-project]");
    $("[data-weight-range]", card).value = el.value;
    updateWeight();
  }
  if (el.form?.id === "adjust-form") {
    if (el.name === "score_range") el.form.elements.score.value = el.value;
    if (el.name === "score") el.form.elements.score_range.value = el.value;
  }
});
document.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.matches("[data-settings-form]")) return;
  const submitter = event.submitter;
  const data = new FormData(form);
  if (submitter) submitter.disabled = true;
  try {
    if (form.getAttribute("id") === "login-form") {
      await api("/login", "POST", { password: data.get("password") });
      state.meta = await api("/meta");
      await refresh();
      overview();
    } else if (form.getAttribute("id") === "member-form") {
      if (
        ["github_ids", "confluence_ids"].some((key) =>
          String(data.get(key + "_entry")).trim()
        )
      ) {
        throw new Error("입력 중인 ID의 추가 버튼을 누른 뒤 저장하세요.");
      }
      const body = {
        id: data.get("id"),
        name: data.get("name"),
        github_ids: state.memberDraft.github_ids,
        confluence_ids: state.memberDraft.confluence_ids,
        career_years: Number(data.get("career_years")),
        career_reference_year: Number(data.get("career_reference_year")),
        active: data.has("active"),
      };
      await api(
        "/members" +
          (form.dataset.mid ? "/" + encodeURIComponent(form.dataset.mid) : ""),
        form.dataset.mid ? "PUT" : "POST",
        body
      );
      closeModal();
      await refresh();
      if (state.view === "editor") editorView();
      else membersView();
      toast("팀원 정보를 저장했습니다.");
    } else if (form.getAttribute("id") === "adjust-form") {
      state.evaluation = await api(
        "/evaluations/" + state.evaluation.id + "/adjust",
        "PATCH",
        {
          revision: state.evaluation.revision,
          member_id: state.selectedMember,
          project_id: state.selectedProject,
          dimension: form.dataset.dim,
          score: data.has("unassessed") ? null : Number(data.get("score")),
          reason: data.get("reason"),
          comment: data.get("comment"),
          reset: submitter?.name === "reset",
        }
      );
      closeModal();
      detailView();
      toast("프로젝트 점수와 종합점수를 다시 계산했습니다.");
    } else if (form.getAttribute("id") === "decision-form") {
      await api(
        "/evaluations/" + state.evaluation.id + "/" + form.dataset.decision,
        "POST",
        { revision: state.evaluation.revision, reason: data.get("reason") }
      );
      closeModal();
      await openEvaluation(state.evaluation.id);
      toast("평가 상태를 변경했습니다.");
    }
  } catch (e) {
    if (form.getAttribute("id") === "login-form")
      $("#login-error").textContent = e.message;
    else toast(e.message);
  } finally {
    if (submitter) submitter.disabled = false;
  }
});
(async () => {
  try {
    state.meta = await api("/meta");
    await refresh();
    overview();
  } catch (e) {
    loginView();
  }
})();
