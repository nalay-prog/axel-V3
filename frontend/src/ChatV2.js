import React, { useEffect, useMemo, useRef, useState } from "react";
import "./ChatV2.css";

const STORAGE_KEY = "darwin_conversations_v1";
const AUDIT_DETAIL_STORAGE_KEY = "darwin_audit_detail_v1";
const API_URL = process.env.REACT_APP_API_URL || "http://localhost:5050/ask";
const DEFAULT_TITLE = "Nouvelle conversation";
const ASSISTANT_NAME = "Axel";
const ASSISTANT_AVATAR_URL =
  process.env.REACT_APP_ASSISTANT_AVATAR_URL || "/assistant-axel.jpg";
const TOP_PROFILE_AVATAR_URL =
  process.env.REACT_APP_TOP_PROFILE_AVATAR_URL || ASSISTANT_AVATAR_URL;

const DARWIN_INVEST_PROMPT =
  "Présente Darwin Invest en quelques points clairs et propose une première allocation patrimoniale simple.";

const TRAINING_PROMPT =
  "Je souhaite des informations sur la formation Mister IA Darwin pour un CGP.";
const SIMULATOR_URL = "https://www.simul-scpi.com/";

const nowIso = () => new Date().toISOString();

function toText(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (typeof content === "number" || typeof content === "boolean") return String(content);

  if (typeof content === "object" && !Array.isArray(content)) {
    return JSON.stringify(content, null, 2);
  }

  // Arrays (ex: blocks)
  if (Array.isArray(content)) {
    return content
      .map((c) => (typeof c === "string" ? c : c?.text ?? JSON.stringify(c)))
      .join("\n");
  }

  return "";
}

const normalizeContent = (content) => toText(content);

const shortText = (text, maxLen = 46) => {
  const clean = toText(text).replace(/\s+/g, " ").trim();
  if (!clean) return DEFAULT_TITLE;
  return clean.length > maxLen ? `${clean.slice(0, maxLen)}...` : clean;
};

const safeParseConversations = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((conv) => conv && conv.id && Array.isArray(conv.messages))
      .map((conv) => ({
        ...conv,
        title: shortText(conv.title || ""),
        messages: (conv.messages || []).map((msg, index) => ({
          ...msg,
          id: msg?.id || `m_legacy_${conv.id}_${index}`,
          role: msg?.role === "assistant" ? "assistant" : "user",
          content: normalizeContent(msg?.content),
          createdAt: msg?.createdAt || nowIso(),
        })),
      }));
  } catch (_error) {
    return [];
  }
};

const safeParseBoolean = (key, defaultValue = false) => {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined) return defaultValue;
    return raw === "1" || raw === "true";
  } catch (_error) {
    return defaultValue;
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
    .map((msg) => ({ role: msg.role, content: toText(msg.content) }));

const isReportLikeMessage = (content = "") => {
  const text = toText(content).trim(); // ✅ plus de crash
  if (!text) return false;
  return text.includes("ANALYSE:") || text.includes("CONCLUSION:");
};

const renderInlineMarkdown = (line, keyPrefix) => {
  const text = String(line || "");
  const tokenRegex = /(\*\*[^*\n]+?\*\*|https?:\/\/[^\s<>()]+)/g;
  const nodes = [];
  let lastIndex = 0;
  let partIndex = 0;
  let match = tokenRegex.exec(text);

  while (match) {
    const token = match[0];
    if (match.index > lastIndex) {
      nodes.push(
        <React.Fragment key={`${keyPrefix}_txt_${partIndex++}`}>
          {text.slice(lastIndex, match.index)}
        </React.Fragment>
      );
    }

    if (token.startsWith("**") && token.endsWith("**")) {
      const boldValue = token.slice(2, -2).trim();
      nodes.push(<strong key={`${keyPrefix}_bold_${partIndex++}`}>{boldValue}</strong>);
    } else {
      nodes.push(
        <a
          key={`${keyPrefix}_url_${partIndex++}`}
          className="v2-md-link"
          href={token}
          target="_blank"
          rel="noopener noreferrer"
        >
          {token}
        </a>
      );
    }

    lastIndex = tokenRegex.lastIndex;
    match = tokenRegex.exec(text);
  }

  if (lastIndex < text.length) {
    nodes.push(
      <React.Fragment key={`${keyPrefix}_tail_${partIndex++}`}>
        {text.slice(lastIndex)}
      </React.Fragment>
    );
  }

  return nodes.length > 0 ? nodes : text;
};

const renderMessageContent = (content, isReportMessage) => {
  const safeContent = toText(content);
  const lines = safeContent.split(/\r?\n/);
  const blocks = [];
  let listItems = [];

  const flushList = (listKey) => {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={`ul_${listKey}`} className="v2-md-list">
        {listItems.map((item, index) => (
          <li key={`li_${listKey}_${index}`} className="v2-md-list-item">
            {renderInlineMarkdown(item, `li_${listKey}_${index}`)}
          </li>
        ))}
      </ul>
    );
    listItems = [];
  };

  lines.forEach((rawLine, index) => {
    const line = String(rawLine || "");
    const trimmed = line.trim();

    if (!trimmed) {
      flushList(index);
      blocks.push(<div key={`sp_${index}`} className="v2-md-spacer" />);
      return;
    }

    const bulletMatch = trimmed.match(/^[-*•]\s+(.+)$/);
    if (bulletMatch) {
      listItems.push(bulletMatch[1]);
      return;
    }

    flushList(index);

    const titleMatch = trimmed.match(/^#{1,6}\s+(.+)$/);
    const lineText = titleMatch ? titleMatch[1] : line;
    blocks.push(
      <p key={`line_${index}`} className={`v2-md-line ${titleMatch ? "v2-md-line-title" : ""}`}>
        {renderInlineMarkdown(lineText, `line_${index}`)}
      </p>
    );
  });

  flushList("tail");

  if (blocks.length === 0) {
    return <div className="v2-message-text">{safeContent}</div>;
  }

  return <div className={`v2-message-text ${isReportMessage ? "v2-report-text" : ""}`}>{blocks}</div>;
};

function RenderMessage({ content, isReportMessage }) {
  return renderMessageContent(toText(content), isReportMessage);
}

/* ============================================================================
   ✅ AJOUT INTELLIGENT: barre de chargement + pourcentage (sans backend streaming)
   - monte vite puis ralentit
   - plafonne à 92% jusqu'à la réponse
   - passe à 100% quand la réponse arrive
============================================================================ */
function useSmartProgress() {
  const [active, setActive] = useState(false);
  const [percent, setPercent] = useState(0);
  const intervalRef = useRef(null);

  const start = () => {
    if (intervalRef.current) window.clearInterval(intervalRef.current);
    setActive(true);
    setPercent(0);

    const startedAt = Date.now();
    intervalRef.current = window.setInterval(() => {
      const elapsed = Date.now() - startedAt;

      let next;
      if (elapsed < 2500) {
        next = (elapsed / 2500) * 65;
      } else if (elapsed < 9000) {
        next = 65 + ((elapsed - 2500) / 6500) * 25;
      } else {
        next = 92;
      }

      // petit bruit visuel (+0..1) pour éviter sensation “bloquée”
      next = Math.min(92, Math.max(0, next + Math.random() * 1));

      setPercent((prev) => (next > prev ? next : prev));
    }, 120);
  };

  const finish = async () => {
    if (intervalRef.current) window.clearInterval(intervalRef.current);
    setPercent(100);
    await new Promise((r) => setTimeout(r, 220));
    setActive(false);
    setPercent(0);
  };

  const stop = () => {
    if (intervalRef.current) window.clearInterval(intervalRef.current);
    setActive(false);
    setPercent(0);
  };

  useEffect(() => {
    return () => {
      if (intervalRef.current) window.clearInterval(intervalRef.current);
    };
  }, []);

  return { active, percent: Math.round(percent), start, finish, stop };
}

function LoadingBar({ active, percent }) {
  if (!active) return null;

  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 30,
        background: "rgba(255,255,255,0.85)",
        backdropFilter: "blur(6px)",
        padding: "10px 0 6px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div
          style={{
            flex: 1,
            height: 8,
            background: "rgba(0,0,0,0.08)",
            borderRadius: 999,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${percent}%`,
              height: "100%",
              transition: "width 120ms linear",
              background: "rgba(0,0,0,0.65)",
            }}
          />
        </div>
        <div style={{ width: 46, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
          {percent}%
        </div>
      </div>
      <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>
        Recherche & synthèse en cours…
      </div>
    </div>
  );
}

function ChatV2() {
  const [conversations, setConversations] = useState(() => safeParseConversations());
  const [activeConversationId, setActiveConversationId] = useState(() => {
    const initial = safeParseConversations();
    return initial[0]?.id || null;
  });
  const [searchTerm, setSearchTerm] = useState("");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [assistantAvatarError, setAssistantAvatarError] = useState(false);
  const [profileAvatarError, setProfileAvatarError] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [auditDetail, setAuditDetail] = useState(() =>
    safeParseBoolean(AUDIT_DETAIL_STORAGE_KEY, false)
  );

  // ✅ AJOUT: progress UI
  const progress = useSmartProgress();

  const messagesViewportRef = useRef(null);
  const textareaRef = useRef(null);
  const welcomeViewportRef = useRef(null);
  const shouldAutoScrollRef = useRef(true);
  const forceStickToBottomRef = useRef(false);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  }, [conversations]);

  useEffect(() => {
    localStorage.setItem(AUDIT_DETAIL_STORAGE_KEY, auditDetail ? "1" : "0");
  }, [auditDetail]);

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth > 900) {
        setMobileSidebarOpen(false);
      }
    };

    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const sortedConversations = useMemo(
    () =>
      [...conversations].sort(
        (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
      ),
    [conversations]
  );

  const filteredConversations = useMemo(() => {
    const query = searchTerm.trim().toLowerCase();
    if (!query) return sortedConversations;
    return sortedConversations.filter((conv) =>
      (conv.title || "").toLowerCase().includes(query)
    );
  }, [searchTerm, sortedConversations]);

  const activeConversation = useMemo(
    () => conversations.find((conv) => conv.id === activeConversationId) || null,
    [conversations, activeConversationId]
  );

  const isEmpty = !activeConversation || activeConversation.messages.length === 0;

  const scrollToBottom = (behavior = "smooth") => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({
      top: viewport.scrollHeight,
      behavior,
    });
    setShowJumpToBottom(false);
  };

  const getDistanceToBottom = () => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return 0;
    return viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  };

  const handleMessagesScroll = () => {
    const distanceToBottom = getDistanceToBottom();
    shouldAutoScrollRef.current = distanceToBottom <= 120;
    setShowJumpToBottom(distanceToBottom > 180);
  };

  const scrollElementByDelta = (element, deltaY) => {
    if (!element) return false;
    if (element.scrollHeight <= element.clientHeight + 1) return false;
    const atTop = element.scrollTop <= 0;
    const atBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 1;
    if ((deltaY < 0 && atTop) || (deltaY > 0 && atBottom)) return false;
    element.scrollTop += deltaY;
    return true;
  };

  const handleFrameWheel = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    // Laisse les zones scrollables natives gérer leur propre molette.
    if (
      target.closest(".v2-messages") ||
      target.closest(".v2-conv-box") ||
      target.closest(".v2-welcome")
    ) {
      return;
    }

    // Ne pas perturber la saisie seulement si le champ peut réellement défiler.
    const formField = target.closest("textarea, input, select");
    if (formField) {
      const fieldCanScroll =
        formField.scrollHeight > formField.clientHeight + 1 &&
        getComputedStyle(formField).overflowY !== "hidden";
      if (fieldCanScroll) {
        return;
      }
    }

    let consumed = false;
    if (!isEmpty) {
      consumed = scrollElementByDelta(messagesViewportRef.current, event.deltaY);
      if (consumed) {
        handleMessagesScroll();
      }
    } else {
      consumed = scrollElementByDelta(welcomeViewportRef.current, event.deltaY);
    }

    if (consumed) {
      event.preventDefault();
    }
  };

  useEffect(() => {
    shouldAutoScrollRef.current = true;
    forceStickToBottomRef.current = true;
    setShowJumpToBottom(false);
    requestAnimationFrame(() => scrollToBottom("auto"));
  }, [activeConversationId]);

  useEffect(() => {
    const shouldStick = shouldAutoScrollRef.current || forceStickToBottomRef.current;
    if (!shouldStick) return;
    const isForced = forceStickToBottomRef.current;
    const behavior = isForced ? "auto" : "smooth";
    const raf = requestAnimationFrame(() => {
      scrollToBottom(behavior);
      forceStickToBottomRef.current = false;
    });
    const settle = window.setTimeout(() => {
      if (shouldAutoScrollRef.current) {
        scrollToBottom("auto");
      }
    }, 80);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(settle);
    };
  }, [activeConversation?.messages?.length, loading]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    const clampedHeight = Math.min(textarea.scrollHeight, 180);
    textarea.style.height = `${Math.max(clampedHeight, 52)}px`;
  }, [question, activeConversationId]);

  const appendMessage = (conversationId, message, maybeNewTitle = null) => {
    const normalizedMessage = {
      ...message,
      content: normalizeContent(message?.content),
    };
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
          messages: [...conv.messages, normalizedMessage],
        };
      })
    );
  };

  const createAndActivateConversation = (seedQuestion = "") => {
    const conversation = createConversation(seedQuestion);
    setConversations((prev) => [conversation, ...prev]);
    setActiveConversationId(conversation.id);
    return conversation;
  };

  const sendQuestion = async ({ contentOverride = null, forceAgent = null } = {}) => {
    const content = (contentOverride ?? question).trim();
    if (!content || loading) return;

    let targetConversation = activeConversation;
    if (!targetConversation) {
      targetConversation = createAndActivateConversation(content);
    }

    const userMessage = {
      id: `m_${Date.now()}_u`,
      role: "user",
      content,
      createdAt: nowIso(),
    };

    const historyPayload = toHistoryPayload(targetConversation.messages);
    shouldAutoScrollRef.current = true;
    forceStickToBottomRef.current = true;
    appendMessage(targetConversation.id, userMessage, shortText(content));
    setLoading(true);

    // ✅ AJOUT: démarre la barre de progression au moment du fetch
    progress.start();

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
          force_agent: forceAgent || undefined,
          audit_detail: auditDetail,
        }),
      });

      const data = await res.json();
      const strictRenderedText =
        typeof data?.result_structured_v2?.rendered_text === "string"
          ? data.result_structured_v2.rendered_text.trim()
          : "";
      const assistantRawContent = data.error
        ? `Erreur: ${data.error}`
        : strictRenderedText || data.result_text || data.result || "Pas de réponse disponible.";
      const assistantMessage = {
        id: `m_${Date.now()}_a`,
        role: "assistant",
        content: normalizeContent(assistantRawContent),
        createdAt: nowIso(),
        sources: data.sources || [],
      };

      appendMessage(targetConversation.id, assistantMessage);
    } catch (_error) {
      appendMessage(targetConversation.id, {
        id: `m_${Date.now()}_a`,
        role: "assistant",
        content: "Erreur de connexion avec l'agent. Vérifie que le serveur est lancé.",
        createdAt: nowIso(),
      });
    } finally {
      setLoading(false);
      // ✅ AJOUT: termine la progression quoi qu'il arrive
      await progress.finish();
    }
  };

  const handleAsk = async () => {
    await sendQuestion();
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  const handleNewConversation = () => {
    createAndActivateConversation();
    setQuestion("");
    setMobileSidebarOpen(false);
  };

  const handleSelectConversation = (conversationId) => {
    setActiveConversationId(conversationId);
    setMobileSidebarOpen(false);
  };

  const handleSimulateInvestment = () => {
    setMobileSidebarOpen(false);
    window.open(SIMULATOR_URL, "_blank", "noopener,noreferrer");
  };

  const handleDarwinInvest = async () => {
    setMobileSidebarOpen(false);
    await sendQuestion({ contentOverride: DARWIN_INVEST_PROMPT });
  };

  const handleTrainingInfo = async () => {
    await sendQuestion({ contentOverride: TRAINING_PROMPT });
  };

  const handleQuickQuestion = async (prompt) => {
    await sendQuestion({ contentOverride: prompt });
  };

  const handleLogout = () => {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(AUDIT_DETAIL_STORAGE_KEY);
    setConversations([]);
    setActiveConversationId(null);
    setQuestion("");
    setSearchTerm("");
    setAuditDetail(false);
    setMobileSidebarOpen(false);
  };

  return (
    <div className="v2-shell">
      <div className="v2-frame" onWheelCapture={handleFrameWheel}>
        <aside className={`v2-sidebar ${mobileSidebarOpen ? "is-open" : ""}`}>
          <div className="v2-sidebar-top">
            <div className="v2-logo">DARWIN</div>

            <button type="button" className="v2-nav-pill is-active" aria-label="Vos conversations">
              <span className="v2-nav-icon">✎</span>
              <span>Vos conversations</span>
            </button>

            <label className="v2-search-row" aria-label="Recherche">
              <span className="v2-nav-icon">⌕</span>
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Recherche"
              />
            </label>

            <div className="v2-side-title">Vos conversations</div>

            <div className="v2-conv-box">
              {filteredConversations.length === 0 ? (
                <div className="v2-empty-side">Aucune conversation</div>
              ) : (
                filteredConversations.map((conv) => (
                  <button
                    type="button"
                    key={conv.id}
                    className={`v2-conv-item ${conv.id === activeConversationId ? "is-active" : ""}`}
                    onClick={() => handleSelectConversation(conv.id)}
                  >
                    {conv.title || DEFAULT_TITLE}
                  </button>
                ))
              )}
            </div>
          </div>

          <div className="v2-sidebar-footer">
            <button type="button" className="v2-simulate-btn" onClick={handleSimulateInvestment}>
              Simuler un investissement
            </button>

            <button type="button" className="v2-invest-link" onClick={handleDarwinInvest}>
              Darwin invest
            </button>

            <div className="v2-footer-divider" />

            <button type="button" className="v2-logout-btn" onClick={handleLogout}>
              Déconnexion
            </button>
          </div>
        </aside>

        <button
          type="button"
          className={`v2-sidebar-backdrop ${mobileSidebarOpen ? "is-visible" : ""}`}
          aria-label="Fermer le menu"
          onClick={() => setMobileSidebarOpen(false)}
        />

        <main className="v2-main">
          <header className="v2-topbar">
            <div className="v2-top-left">
              <button
                type="button"
                className="v2-menu-toggle"
                aria-label="Ouvrir le menu"
                onClick={() => setMobileSidebarOpen(true)}
              >
                ☰
              </button>
              <h1>Axel V.1</h1>
            </div>

            <div className="v2-top-right">
              <button
                type="button"
                className={`v2-audit-toggle ${auditDetail ? "is-on" : ""}`}
                aria-pressed={auditDetail}
                title="Activer le mode Audit / Détail"
                onClick={() => setAuditDetail((prev) => !prev)}
              >
                <span className="v2-audit-toggle-label">Audit / Détail</span>
                <span className="v2-audit-toggle-track">
                  <span className="v2-audit-toggle-knob" />
                </span>
              </button>

              <button type="button" className="v2-kebab-btn" aria-label="Options">
                <span />
                <span />
                <span />
              </button>

              <div className="v2-profile-wrap">
                {!profileAvatarError ? (
                  <img
                    src={TOP_PROFILE_AVATAR_URL}
                    alt="Profil"
                    className="v2-profile-avatar"
                    onError={() => setProfileAvatarError(true)}
                  />
                ) : (
                  <div className="v2-profile-fallback">{ASSISTANT_NAME[0]}</div>
                )}
              </div>
            </div>
          </header>

          <section className="v2-content">
            <div className="v2-content-inner">
              {isEmpty ? (
                <div className="v2-welcome" ref={welcomeViewportRef}>
                  <div className="v2-avatar-wrap">
                    {!assistantAvatarError ? (
                      <img
                        src={ASSISTANT_AVATAR_URL}
                        alt={`${ASSISTANT_NAME} assistant`}
                        className="v2-avatar"
                        onError={() => setAssistantAvatarError(true)}
                      />
                    ) : (
                      <div className="v2-avatar-fallback">{ASSISTANT_NAME[0]}</div>
                    )}
                  </div>

                  <h2>Bonjour je suis {ASSISTANT_NAME} votre assistant</h2>
                  <p>en conseil de gestion de patrimoine</p>

                  <div className="v2-welcome-actions">
                    <button type="button" onClick={handleSimulateInvestment}>
                      Simuler un investissement
                    </button>
                    <button type="button" onClick={() => handleQuickQuestion(DARWIN_INVEST_PROMPT)}>
                      Découvrir Darwin invest
                    </button>
                    <button type="button" onClick={() => handleQuickQuestion(TRAINING_PROMPT)}>
                      Formation IA Darwin
                    </button>
                  </div>
                </div>
              ) : (
                <div className="v2-messages-shell">
                  <div className="v2-messages" ref={messagesViewportRef} onScroll={handleMessagesScroll}>
                    {/* ✅ AJOUT: barre de chargement */}
                    <LoadingBar active={progress.active} percent={progress.percent} />

                    {activeConversation.messages.map((msg) => {
                      const isReportMessage =
                        msg.role === "assistant" && isReportLikeMessage(msg.content);

                      return (
                        <article
                          key={msg.id}
                          className={`v2-message v2-message-animate ${msg.role === "user" ? "v2-user-message" : "v2-assistant-message"} ${isReportMessage ? "v2-report-message" : ""}`}
                        >
                          <div className="v2-message-role">{msg.role === "user" ? "Vous" : ASSISTANT_NAME}</div>
                          <RenderMessage content={msg.content} isReportMessage={isReportMessage} />

                          {msg.role === "assistant" && Array.isArray(msg.sources) && msg.sources.length > 0 && (
                            <div className="v2-message-sources">
                              {msg.sources.slice(0, 4).map((src, idx) => (
                                <span key={`${msg.id}_${idx}`} className="v2-source-chip">
                                  {typeof src === "string" ? src : src?.metadata?.source || "document"}
                                </span>
                              ))}
                            </div>
                          )}
                        </article>
                      );
                    })}

                    {loading && (
                      <article className="v2-message v2-assistant-message v2-message-animate">
                        <div className="v2-message-role">{ASSISTANT_NAME}</div>
                        <div className="v2-typing">Analyse en cours...</div>
                      </article>
                    )}
                  </div>

                  {showJumpToBottom && (
                    <button
                      type="button"
                      className="v2-jump-bottom"
                      onClick={() => scrollToBottom("smooth")}
                    >
                      Revenir en bas
                    </button>
                  )}
                </div>
              )}
            </div>
          </section>

          <footer className="v2-composer-wrap">
            <div className="v2-composer">
              <textarea
                ref={textareaRef}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="En conseil de gestion de patrimoine<..."
                disabled={loading}
                rows={3}
              />

              <div className="v2-composer-actions">
                <button type="button" className="v2-round v2-plus" aria-label="Nouveau chat" onClick={handleNewConversation}>
                  +
                </button>

                <button
                  type="button"
                  className="v2-round v2-send"
                  onClick={handleAsk}
                  disabled={loading || !question.trim()}
                  aria-label="Envoyer"
                >
                  ↑
                </button>
              </div>
            </div>

            <div className="v2-composer-hint">
              {auditDetail
                ? "Mode Audit / Détail activé: score breakdown, pondérations, données utilisées et dates."
                : "Entrée pour envoyer, Shift+Entrée pour sauter une ligne."}
            </div>

            <button type="button" className="v2-training-chip" onClick={handleTrainingInfo}>
              <span className="v2-chip-dot">◎</span>
              Formation Mister IA Darwin, comment implémenter l'IA au quotidien en tant que CGP,
              inscrivez-vous :)
            </button>
          </footer>
        </main>
      </div>
    </div>
  );
}

export default ChatV2;