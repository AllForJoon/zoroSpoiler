const settings = document.querySelector("fieldset");
const status = document.getElementById("status");

async function loadSettings() {
  try {
    const { modelSource } = await chrome.storage.local.get("modelSource");
    const saved = modelSource === "local" ? "local" : "remote";
    settings.querySelector(`input[value="${saved}"]`).checked = true;
    settings.disabled = false;
  } catch {
    status.textContent = "연결 설정을 불러오지 못했습니다.";
  }
}

settings.addEventListener("change", async ({ target }) => {
  try {
    await chrome.storage.local.set({ modelSource: target.value });
    status.textContent = "연결 설정을 저장했습니다.";
  } catch {
    status.textContent = "연결 설정을 저장하지 못했습니다.";
    await loadSettings();
  }
});

loadSettings();
