document.addEventListener("DOMContentLoaded", () => {
  const statusRestart = document.getElementById("status-restart");
  const statusPortal = document.getElementById("status-portal");
  const accountSelect = document.getElementById("account-select");
  const includeResolved = document.getElementById("include-resolved");
  const syncBtn = document.getElementById("sync-btn");
  const resultBox = document.getElementById("result-box");

  function showResult(text, ok) {
    resultBox.hidden = false;
    resultBox.textContent = text;
    resultBox.className = `result-box ${ok ? "is-ok" : "is-error"}`;
  }

  function refreshTabStatus() {
    chrome.runtime.sendMessage({ type: "CHECK_TABS_STATUS" }, (resp) => {
      if (!resp || !resp.ok) {
        statusRestart.textContent = "Ошибка";
        statusPortal.textContent = "Ошибка";
        return;
      }
      statusRestart.textContent = resp.hasRestartTab ? "Открыта" : "Не найдена";
      statusRestart.className = resp.hasRestartTab ? "status-ok" : "status-miss";

      statusPortal.textContent = resp.hasPortalTab ? "Открыта" : "Не найдена";
      statusPortal.className = resp.hasPortalTab ? "status-ok" : "status-miss";
    });
  }

  refreshTabStatus();

  syncBtn.addEventListener("click", () => {
    syncBtn.disabled = true;
    syncBtn.textContent = "Синхронизация...";
    showResult("Запрашиваем список товаров и опрашиваем OTTO Partner Connect...", true);

    chrome.runtime.sendMessage(
      {
        type: "RUN_SYNC_FROM_POPUP",
        account: accountSelect.value,
        includeResolved: Boolean(includeResolved.checked),
      },
      (resp) => {
        syncBtn.disabled = false;
        syncBtn.textContent = "Забрать фото сейчас";
        refreshTabStatus();

        if (!resp || !resp.ok) {
          showResult(resp?.error || "Ошибка синхронизации.", false);
          return;
        }
        const s = resp.summary;
        if (s.totalMissing === 0) {
          showResult(`Аккаунт ${s.account}: товаров без фото не найдено.`, true);
          return;
        }
        showResult(
          `Аккаунт ${s.account}: без фото было ${s.totalMissing}, найдено на портале ${s.matchedCount}, обновлено в БД ${s.updatedCount}, сохранено картинок ${s.savedAssetsCount}.`,
          true,
        );
      },
    );
  });
});
