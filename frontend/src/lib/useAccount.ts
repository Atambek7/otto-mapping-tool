export function useAccount() {
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem("current_account", "JV");
    } catch (e) {}
  }
  return { account: "JV" as const, setAccount: (_acc?: any) => {} };
}
