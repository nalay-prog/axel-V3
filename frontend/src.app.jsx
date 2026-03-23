import React, { useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ArrowRight } from "lucide-react";

export default function Home() {
  const [question, setQuestion] = useState("");
  const [response, setResponse] = useState(null);
  const [sources, setSources] = useState([]);
  const [agentUsed, setAgentUsed] = useState("");
  const [loading, setLoading] = useState(false);

  // ✅ session_id stable tant que tu ne refresh pas la page
  const [sessionId] = useState(() => crypto.randomUUID());

  const handleAsk = async () => {
    if (!question) return;

    setLoading(true);
    setResponse(null);
    setSources([]);
    setAgentUsed("");

    try {
      const res = await fetch("http://127.0.0.1:5050/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          session_id: sessionId, // ✅ mémoire
        }),
      });

      const data = await res.json();
      setResponse(data.result || "Aucune réponse.");
      setSources(Array.isArray(data.sources) ? data.sources : []);
      setAgentUsed(data.agent_used || "");
    } catch (error) {
      setResponse("Erreur lors de la requête.");
      setSources([]);
      setAgentUsed("");
    }

    setLoading(false);
  };

  // Optionnel: reset mémoire (nouvelle conversation)
  const handleReset = async () => {
    await fetch("http://127.0.0.1:5050/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });

    setQuestion("");
    setResponse(null);
    setSources([]);
    setAgentUsed("");
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-black via-[#0f1d16] to-[#183321] text-white relative overflow-hidden">
      <div className="absolute top-6 left-6 font-bold text-2xl">DARWIN</div>

      <div className="absolute top-6 left-1/2 transform -translate-x-1/2 flex gap-4">
        <Button variant="ghost" className="text-white/70">le simulateur</Button>
        <Button variant="ghost" className="text-white/70">à propos</Button>
      </div>

      <div className="absolute top-6 right-6 flex gap-2">
        <Button
          variant="ghost"
          className="text-white/70 border border-white/20"
          onClick={handleReset}
        >
          Nouvelle conversation
        </Button>

        <Button className="bg-gradient-to-r from-[#c0ff9e] to-[#98e670] text-black rounded-full px-6">
          Prendre rendez-vous
        </Button>
      </div>

      <h1
        className="text-4xl md:text-5xl font-light text-center mb-16 z-10 font-poppins tracking-tight text-white/70"
        style={{ letterSpacing: "-1px" }}
      >
        Le meilleur ami des <span className="italic text-white">CGP</span>
      </h1>

      <div className="absolute z-0 w-full flex justify-center items-center animate-pulse">
        <div className="relative w-[600px] h-[300px]">
          <div className="absolute inset-0 rounded-[100px] border border-[#e1ffbc]/15 scale-125" />
          <div className="absolute inset-4 rounded-[90px] border border-[#e1ffbc]/20 scale-110" />
          <div className="absolute inset-8 rounded-[80px] border border-[#e1ffbc]/25 scale-100" />
          <div className="absolute inset-12 rounded-[70px] border border-[#e1ffbc]/30 scale-90" />
          <div className="absolute inset-16 rounded-[60px] border border-[#e1ffbc]/40 scale-75" />
        </div>
      </div>

      <div className="relative w-[80%] max-w-xl z-10">
        <div className="absolute inset-0 rounded-full bg-[#e1ffbc]/10 blur-2xl" />
        <div className="relative z-10 flex bg-[#0f1d16] border border-[#e1ffbc]/20 rounded-full overflow-hidden">
          <Input
            type="text"
            placeholder="Comment je peux vous aider aujourd'hui ?"
            className="bg-transparent text-white px-6 py-4 flex-1 placeholder:text-white/50"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleAsk();
            }}
          />
          <Button onClick={handleAsk} className="rounded-none px-6">
            {loading ? "..." : <ArrowRight />}
          </Button>
        </div>
      </div>

      {(response || sources.length > 0) && (
        <div className="mt-10 p-6 max-w-2xl bg-white text-black rounded-xl shadow-md z-10">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-semibold">Réponse de l'agent :</h2>
            {agentUsed && (
              <span className="text-xs px-2 py-1 rounded-full bg-black/10">
                agent: {agentUsed}
              </span>
            )}
          </div>

          {response && <p style={{ whiteSpace: "pre-wrap" }}>{response}</p>}

          {sources.length > 0 && (
            <div className="mt-4">
              <h3 className="font-semibold mb-2">Sources</h3>
              <ul className="list-disc pl-5 space-y-1">
                {sources.map((url) => (
                  <li key={url}>
                    <a
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-600 underline break-all"
                    >
                      {url}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}