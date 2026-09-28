function rubricControls(rubric, weights, scope) {
  return `<div class="rubric-controls" data-version="${esc(
    rubric.version
  )}">${rubric.dimensions
    .map(
      (d) =>
        `<section class="criterion-edit" data-criterion="${esc(
          d.id
        )}"><div class="form-grid"><label>기준 이름<input data-rubric-field="name" value="${esc(
          d.name
        )}" required maxlength="100"></label><label>비율 (%)<input data-rubric-field="weight" type="number" value="${
          d.weight
        }" min="0.01" max="100" step="0.01" required></label><label class="full">평가 내용<textarea data-rubric-field="description" required minlength="3">${esc(
          d.description
        )}</textarea></label></div></section>`
    )
    .join("")}<p class="help rubric-total">비율 합계 ${rubric.dimensions.reduce(
    (n, d) => n + d.weight,
    0
  )} / 100</p><label>점수 구간 설명<textarea data-rubric-anchors required minlength="3">${esc(
    rubric.anchors
  )}</textarea></label><div class="form-grid level-inputs">${Object.entries(
    weights
  )
    .map(
      ([key, value]) =>
        `<label>${key} 종합점수 계수<input type="number" data-level-weight="${key}" min="0.01" max="1" step="0.01" value="${value}" required></label>`
    )
    .join("")}</div><p class="help">CL2 > CL3 > CL4 순서, 0 초과 1 이하. ${
    scope === "global"
      ? "기존 평가는 자동으로 바뀌지 않습니다. 평가 편집에서 기본 기준 불러오기로 적용하세요."
      : "비율과 계수 변경은 즉시 재집계합니다. 평가 내용·점수 구간을 바꾸면 재분석이 필요합니다."
  }</p></div>`;
}
function readRubricControls(root) {
  const box = root.querySelector(".rubric-controls");
  return {
    rubric: {
      version: box.dataset.version,
      dimensions: [...box.querySelectorAll("[data-criterion]")].map((el) => ({
        id: el.dataset.criterion,
        name: el.querySelector('[data-rubric-field="name"]').value.trim(),
        description: el
          .querySelector('[data-rubric-field="description"]')
          .value.trim(),
        weight: Number(el.querySelector('[data-rubric-field="weight"]').value),
      })),
      anchors: box.querySelector("[data-rubric-anchors]").value.trim(),
    },
    level_weights: Object.fromEntries(
      [...box.querySelectorAll("[data-level-weight]")].map((el) => [
        el.dataset.levelWeight,
        Number(el.value),
      ])
    ),
  };
}
async function rubricSettingsView() {
  state.view = "rubric";
  state.settings = await api("/settings");
  const settings = state.settings;
  shell(
    `<div class="heading"><div><div class="eyebrow">REVIEW POLICY</div><h1>평가 기준</h1><p>평가 내용·비율·연차별 계수를 편집합니다. 비율의 합은 100이어야 합니다.</p></div><button data-action="nav" data-view="services">서비스 연결 →</button></div><section class="panel"><header class="panel-head"><h2>기본 평가 기준</h2><span class="pill">${esc(
      settings.rubric.version
    )}</span></header><form id="rubric-settings-form" class="panel-body form-stack" data-settings-form>${rubricControls(
      settings.rubric,
      settings.level_policy.weights,
      "global"
    )}<div class="actions"><button class="primary" type="submit">평가 기준 저장</button></div></form></section><div class="banner info">Confluence는 루트 하위 페이지의 버전별 변경분, GitHub는 기간 내 기본 브랜치 커밋의 변경 코드(diff)를 분석합니다. 근거 원문과 LLM 원점수는 보존하고 평가자 조정을 별도로 기록합니다.</div>`
  );
}
async function servicesView() {
  state.view = "services";
  state.settings = await api("/settings");
  shell(
    `<div class="heading"><div><div class="eyebrow">INTEGRATIONS</div><h1>서비스 연결</h1><p>서비스 주소와 인증 정보를 설정하고, 각 서비스의 연결 상태를 직접 확인하세요.</p></div></div>${demoBanner()}<div class="banner info">입력값으로 연결 테스트한 뒤 저장할 수 있습니다. 비밀번호·토큰은 다시 표시하지 않으며, 공란이면 기존 값을 유지합니다. 화면 설정은 서버의 영속 설정 파일에 저장되고 재시작 후에도 유지됩니다.</div>${Object.entries(
      state.settings.services
    )
      .map(
        ([kind, s]) =>
          `<section class="panel"><header class="panel-head"><h2>${
            kind === "github"
              ? "GitHub"
              : kind === "confluence"
              ? "Confluence"
              : "LLM"
          }</h2><span class="pill secret-state">${
            s.secret_set ? "인증키 저장됨" : "인증키 없음"
          }</span></header><form class="panel-body form-stack" data-settings-form data-service="${kind}"><div class="form-grid"><label class="full">API URL<input name="api_url" type="url" value="${esc(
            s.api_url
          )}" required></label>${
            kind !== "llm"
              ? `<label class="full">웹 URL<input name="web_url" type="url" value="${esc(
                  s.web_url
                )}" required></label><label>인증 방식<select name="auth"><option value="bearer" ${
                  s.auth === "bearer" ? "selected" : ""
                }>Bearer 토큰</option><option value="basic" ${
                  s.auth === "basic" ? "selected" : ""
                }>Basic (사용자 + 토큰)</option></select></label><label>사용자 이름 / 이메일 (Basic)<input name="username" value="${esc(
                  s.username
                )}" autocomplete="off"></label>`
              : `<label class="full">모델<input name="model" value="${esc(
                  s.model
                )}" required></label><label class="checkbox-line"><input type="checkbox" name="json_mode" ${
                  s.json_mode ? "checked" : ""
                }>JSON 출력 모드 사용</label>`
          }<label class="full">${
            kind === "llm" ? "API Key" : "토큰"
          }<input name="secret" type="password" autocomplete="new-password" placeholder="${
            s.secret_set
              ? "저장된 인증키 유지 · 변경할 때만 입력"
              : "새 인증키 입력"
          }"></label><label class="checkbox-line"><input name="clear_secret" type="checkbox">저장된 인증키 삭제</label></div><p class="help">${
            kind === "llm"
              ? "연결 테스트는 선택한 모델에 짧은 요청을 보내므로 소량의 API 사용량이 발생할 수 있습니다."
              : "연결 테스트는 사용자 인증을 확인합니다. 프로젝트별 저장소·페이지 접근 권한은 평가 수집 시 확인합니다."
          }</p><div class="connection-status help" aria-live="polite">연결을 테스트하지 않았습니다.</div><div class="actions"><button type="button" data-settings-action="test">연결 테스트</button><button type="submit" class="primary">연결 설정 저장</button></div></form></section>`
      )
      .join("")}`
  );
}
function servicePayload(form) {
  const data = new FormData(form);
  return {
    revision: state.settings.revision,
    api_url: data.get("api_url"),
    web_url: data.get("web_url") || "",
    auth: data.get("auth") || "bearer",
    username: data.get("username") || "",
    secret: data.get("secret") || null,
    clear_secret: data.has("clear_secret"),
    model: data.get("model") || "",
    json_mode: data.has("json_mode"),
  };
}
function reviewTextModal() {
  const e = state.evaluation;
  const r = e.results.find(
    (r) =>
      r.project_id === state.selectedProject &&
      r.member_id === state.selectedMember
  );
  showModal(
    `<h2>평가 의견 편집</h2><form id="review-text-form" data-settings-form class="form-stack"><label>기여 요약<textarea name="summary">${esc(
      r.adjusted_summary ?? r.summary
    )}</textarea></label><label>판단의 한계 / 참고 의견<textarea name="limitations">${esc(
      r.adjusted_limitations ?? r.limitations
    )}</textarea></label><label>수정 사유<textarea name="reason" minlength="3" required></textarea></label><p class="help">LLM 원문은 이력과 결과 데이터에 보존됩니다. 확정된 평가는 저장하면 검토 상태로 바뀝니다.</p><div class="actions"><button type="button" data-action="close-modal">취소</button><button type="submit" class="primary">의견 저장</button></div></form>`
  );
}
function reanalyzeModal() {
  showModal(
    `<h2>전체 다시 분석</h2><form id="reanalyze-form" data-settings-form class="form-stack"><p class="help">현재 평가의 근거와 기준으로 자료를 다시 수집하고 모든 LLM 판정을 새로 요청합니다. 기존 실행 결과는 이력에 보존하고, 유효한 수동 점수·의견 보정은 유지합니다.</p><label>재분석 사유<textarea name="reason" required minlength="3"></textarea></label><div class="actions"><button type="button" data-action="close-modal">취소</button><button class="primary" type="submit">전체 재분석 시작</button></div></form>`
  );
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest('[data-settings-action="test"]');
  if (!button) return;
  const form = button.closest("form");
  if (!form.reportValidity()) return;
  button.disabled = true;
  const status = form.querySelector(".connection-status");
  status.textContent = "연결 확인 중…";
  try {
    const payload = servicePayload(form);
    const signature = JSON.stringify(payload);
    const result = await api(
      "/settings/services/" + form.dataset.service + "/test",
      "POST",
      payload
    );
    if (JSON.stringify(servicePayload(form)) !== signature) {
      status.textContent = "입력값이 변경되었습니다. 다시 연결 테스트하세요.";
      return;
    }
    status.textContent =
      result.message +
      (result.elapsed_ms != null ? ` · ${result.elapsed_ms}ms` : "");
    status.dataset.ok = String(result.ok);
  } catch (error) {
    status.textContent = error.message;
    status.dataset.ok = "false";
  } finally {
    button.disabled = false;
  }
});
document.addEventListener("input", (event) => {
  const controls = event.target.closest(".rubric-controls");
  if (controls) {
    const total = [
      ...controls.querySelectorAll('[data-rubric-field="weight"]'),
    ].reduce((n, e) => n + Number(e.value), 0);
    controls.querySelector(
      ".rubric-total"
    ).textContent = `비율 합계 ${total.toFixed(2)} / 100`;
  }
  const form = event.target.closest("[data-service]");
  if (form) {
    form.querySelector(".connection-status").textContent =
      "입력값이 변경되었습니다. 연결을 다시 확인하세요.";
    delete form.querySelector(".connection-status").dataset.ok;
  }
});
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!form.matches("[data-settings-form]")) return;
  event.preventDefault();
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    if (form.id === "rubric-settings-form") {
      const input = readRubricControls(form);
      state.settings = await api("/settings/rubric", "PUT", {
        revision: state.settings.revision,
        ...input,
      });
      state.meta = await api("/meta");
      toast("기본 평가 기준을 저장했습니다. 기존 평가는 편집에서 적용하세요.");
      await rubricSettingsView();
    } else if (form.id === "reanalyze-form") {
      state.evaluation = await api(
        "/evaluations/" + state.evaluation.id + "/start",
        "POST",
        {
          revision: state.evaluation.revision,
          force: true,
          reason: new FormData(form).get("reason"),
        }
      );
      closeModal();
      await refresh();
      detailView();
    } else if (form.dataset.service) {
      state.settings = await api(
        "/settings/services/" + form.dataset.service,
        "PUT",
        servicePayload(form)
      );
      state.meta = await api("/meta");
      form.elements.secret.value = "";
      form.elements.clear_secret.checked = false;
      form.closest(".panel").querySelector(".secret-state").textContent = state
        .settings.services[form.dataset.service].secret_set
        ? "인증키 저장됨"
        : "인증키 없음";
      toast("연결 설정을 저장했습니다.");
    } else if (form.id === "review-text-form") {
      const data = new FormData(form);
      state.evaluation = await api(
        "/evaluations/" + state.evaluation.id + "/review-text",
        "PATCH",
        {
          revision: state.evaluation.revision,
          member_id: state.selectedMember,
          project_id: state.selectedProject,
          summary: data.get("summary"),
          limitations: data.get("limitations"),
          reason: data.get("reason"),
        }
      );
      closeModal();
      detailView();
      toast("평가 의견을 저장했습니다.");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
});
