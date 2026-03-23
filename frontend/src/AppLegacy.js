import React, { useEffect, useMemo, useRef, useState } from "react";
import "./AppLegacy.css";

const STORAGE_KEY = "darwin_conversations_v1";
const API_URL = process.env.REACT_APP_API_URL || "https://web-production-6adf6.up.railway.app/ask";
const DEFAULT_TITLE = "Nouvelle conversation";

const nowIso = () => new Date().toISOString();

const shortText = (text, maxLen = 46) => {
  const clean = (text || "").replace(/\s+/g, " ").trim();
  if (!clean) return DEFAULT_TITLE;
  return clean.length > maxLen ? `${clean.slice(0, maxLen)}...` : clean;
};

const messagePreview = (conversation) => {
  const lastUser = [...(conversation.messages || [])]
    .reverse()
    .find((msg) => msg.role === "user");
  return lastUser?.content || "";
};

const safeParseConversations = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((conv) => conv && conv.id && Array.isArray(conv.messages));
  } catch (_error) {
    return [];
  }
};

const createConversation = (seedQuestion = "") => {
  const id = `conv_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const createdAt = nowIso();
  return {
    id,
    sessionId: id,
    title: seedQuestion ? shortText(seedQuestion) : DEFAULT_TITLE,
    createdAt,
    updatedAt: createdAt,
    messages: [],
  };
};

const toHistoryPayload = (messages, maxMessages = 20) =>
  (messages || [])
    .slice(-maxMessages)
    .map((msg) => ({ role: msg.role, content: msg.content }));

const startOfDay = (date) => {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
};

const isSameDay = (a, b) => startOfDay(a).getTime() === startOfDay(b).getTime();

const groupLabel = (isoDate) => {
  const date = new Date(isoDate);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);

  if (isSameDay(date, today)) return "Today";
  if (isSameDay(date, yesterday)) return "Yesterday";
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });
};

function App() {
  const [conversations, setConversations] = useState(() => safeParseConversations());
  const [activeConversationId, setActiveConversationId] = useState(() => {
    const initial = safeParseConversations();
    return initial[0]?.id || null;
  });
  const [question, setQuestion] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesRef = useRef(null);
  const shouldAutoScrollRef = useRef(true);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  }, [conversations]);

  const getDistanceToBottom = () => {
    const viewport = messagesRef.current;
    if (!viewport) return 0;
    return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  };

  const scrollMessagesToBottom = (behavior = "smooth") => {
    const viewport = messagesRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior });
  };

  const handleMessagesScroll = () => {
    const distance = getDistanceToBottom();
    shouldAutoScrollRef.current = distance <= 120;
  };

  const handleConversationWheel = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    if (target.closest(".messages") || target.closest(".sidebar-inner")) {
      return;
    }

    const formField = target.closest("textarea, input, select");
    if (formField) {
      const fieldCanScroll =
        formField.scrollHeight > formField.clientHeight + 1 &&
        getComputedStyle(formField).overflowY !== "hidden";
      if (fieldCanScroll) return;
    }

    const viewport = messagesRef.current;
    if (!viewport) return;
    if (viewport.scrollHeight <= viewport.clientHeight + 1) return;
    viewport.scrollTop += event.deltaY;
    handleMessagesScroll();
    event.preventDefault();
  };

  const sortedConversations = useMemo(
    () =>
      [...conversations].sort(
        (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
      ),
    [conversations]
  );

  const activeConversation = useMemo(
    () => conversations.find((conv) => conv.id === activeConversationId) || null,
    [conversations, activeConversationId]
  );

  const filteredConversations = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    if (!query) return sortedConversations;
    return sortedConversations.filter((conv) => {
      const title = (conv.title || "").toLowerCase();
      const preview = messagePreview(conv).toLowerCase();
      return title.includes(query) || preview.includes(query);
    });
  }, [searchTerm, sortedConversations]);

  const groupedConversations = useMemo(() => {
    const groups = new Map();
    filteredConversations.forEach((conv) => {
      const label = groupLabel(conv.updatedAt || conv.createdAt || nowIso());
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(conv);
    });
    return Array.from(groups.entries());
  }, [filteredConversations]);

  useEffect(() => {
    shouldAutoScrollRef.current = true;
    requestAnimationFrame(() => scrollMessagesToBottom("auto"));
  }, [activeConversationId]);

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return;
    requestAnimationFrame(() => scrollMessagesToBottom("smooth"));
  }, [activeConversation?.messages?.length, loading]);

  const handleNewConversation = () => {
    const conversation = createConversation();
    setConversations((prev) => [conversation, ...prev]);
    setActiveConversationId(conversation.id);
    setQuestion("");
  };

  const appendMessage = (conversationId, message, maybeNewTitle = null) => {
    setConversations((prev) =>
      prev.map((conv) => {
        if (conv.id !== conversationId) return conv;
        const title =
          maybeNewTitle && (conv.title === DEFAULT_TITLE || !conv.title)
            ? maybeNewTitle
            : conv.title;
        return {
          ...conv,
          title,
          updatedAt: nowIso(),
          messages: [...conv.messages, message],
        };
      })
    );
  };

  const handleAsk = async () => {
    const content = question.trim();
    if (!content || loading) return;

    let targetConversation = activeConversation;
    if (!targetConversation) {
      targetConversation = createConversation(content);
      setConversations((prev) => [targetConversation, ...prev]);
      setActiveConversationId(targetConversation.id);
    }

    const userMessage = {
      id: `m_${Date.now()}_u`,
      role: "user",
      content,
      createdAt: nowIso(),
    };

    const historyPayload = toHistoryPayload(targetConversation.messages);
    shouldAutoScrollRef.current = true;
    appendMessage(targetConversation.id, userMessage, shortText(content));
    setLoading(true);
    setQuestion("");

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          question: content,
          session_id: targetConversation.sessionId,
          history: historyPayload,
        }),
      });

      const data = await res.json();

      const strictRenderedText =
        typeof data?.result_structured_v2?.rendered_text === "string"
          ? data.result_structured_v2.rendered_text.trim()
          : "";

      const assistantMessage = {
        id: `m_${Date.now()}_a`,
        role: "assistant",
        content: data.error
          ? `Erreur: ${data.error}`
          : strictRenderedText || data.result_text || data.result || "Pas de réponse disponible.",
        createdAt: nowIso(),
        sources: data.sources || [],
        meta: data.meta || {},
      };
      appendMessage(targetConversation.id, assistantMessage);
    } catch (error) {
      const assistantMessage = {
        id: `m_${Date.now()}_a`,
        role: "assistant",
        content: "Erreur de connexion avec l'agent. Vérifiez que le serveur est lancé.",
        createdAt: nowIso(),
      };
      appendMessage(targetConversation.id, assistantMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  return (
    <div className="shell">
      <div className="ambient-lines">
        <div className="line line-a"></div>
        <div className="line line-b"></div>
        <div className="line line-c"></div>
      </div>

      <header className="topbar">
        <div className="brand">DARWIN</div>
        <div className="top-actions">
          <div className="search-box">
            <span className="search-icon">🔍</span>
            <input
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Search conversations"
            />
          </div>
          <button className="new-btn" onClick={handleNewConversation}>
            <span>+</span> New
          </button>
        </div>
      </header>

      <main className="workspace">
        <aside className="sidebar">
          <div className="sidebar-inner">
            {groupedConversations.length === 0 ? (
              <div className="empty-side">No conversation found</div>
            ) : (
              groupedConversations.map(([label, items]) => (
                <section key={label} className="day-group">
                  <div className="day-title">{label}</div>
                  {items.map((conv) => (
                    <button
                      key={conv.id}
                      className={`conv-item ${conv.id === activeConversationId ? "active" : ""}`}
                      onClick={() => setActiveConversationId(conv.id)}
                    >
                      <div className="conv-title">{conv.title || DEFAULT_TITLE}</div>
                      <div className="conv-preview">{messagePreview(conv) || "No message yet"}</div>
                    </button>
                  ))}
                </section>
              ))
            )}
          </div>
        </aside>

        <section className="conversation-area" onWheelCapture={handleConversationWheel}>
          <div className="messages" ref={messagesRef} onScroll={handleMessagesScroll}>
            {!activeConversation || activeConversation.messages.length === 0 ? (
              <div className="empty-chat">
                <h2>Wealth Manager AI Copilot</h2>
                <p>Lance une conversation pour comparer des solutions patrimoniales.</p>
              </div>
            ) : (
              activeConversation.messages.map((msg) => (
                <article
                  key={msg.id}
                  className={`message ${msg.role === "user" ? "from-user" : "from-assistant"}`}
                >
                  <div className="message-head">
                    {msg.role === "user" ? "Vous" : "Darwin"}
                  </div>
                  <div className="message-body">{msg.content}</div>
                  {msg.role === "assistant" && Array.isArray(msg.sources) && msg.sources.length > 0 && (
                    <div className="message-sources">
                      Sources: {msg.sources.slice(0, 4).map((src, idx) => (
                        <span key={`${msg.id}_src_${idx}`} className="source-chip">
                          {typeof src === "string"
                            ? src
                            : src?.metadata?.source || "document"}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              ))
            )}

            {loading && (
              <article className="message from-assistant">
                <div className="message-head">Darwin</div>
                <div className="typing">
                  <span></span><span></span><span></span>
                </div>
              </article>
            )}
          </div>

          <div className="composer">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Compare SCPI Pierre vs Corum..."
              rows={1}
              disabled={loading}
            />
            <button className="send-btn" onClick={handleAsk} disabled={loading || !question.trim()}>
              {loading ? "..." : "➜"}
            </button>
          </div>
        </section>
      </main>
    </div>
  );
}

export default App;
