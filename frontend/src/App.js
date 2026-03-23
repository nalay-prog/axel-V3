import React from "react";
import AppLegacy from "./AppLegacy";
import ChatV2 from "./ChatV2";

const uiFlag = (process.env.REACT_APP_CHAT_UI || "v2").toLowerCase();
const pathIsV2 =
  typeof window !== "undefined" &&
  window.location.pathname.toLowerCase().startsWith("/chat-v2");

function App() {
  if (uiFlag === "v2" || pathIsV2) {
    return <ChatV2 />;
  }
  return <AppLegacy />;
}

export default App;
