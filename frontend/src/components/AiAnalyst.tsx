import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { fmtRelTime } from "../lib/format";

interface LastAnalysis {
  analysis: { text: string; created_at: string; model: string } | null;
  profile: string;
}

interface SseEvent {
  type: "text" | "thinking" | "progress" | "phase" | "done" | "error";
  delta?: string;
  kind?: string;
  detail?: string;
  message?: string;
  model?: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

async function streamSse(
  url: string,
  body: unknown | null,
  onEvent: (e: SseEvent) => void,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        /* partial frame */
      }
    }
  }
}

function Markdown({ text }: { text: string }) {
  return (
    <div className="ai-md text-[13.5px] leading-relaxed [&_h1]:mt-4 [&_h1]:text-[15px] [&_h1]:font-semibold [&_h2]:mt-4 [&_h2]:text-[14px] [&_h2]:font-semibold [&_h3]:mt-3 [&_h3]:text-[13.5px] [&_h3]:font-semibold [&_p]:mt-2 [&_ul]:mt-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mt-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:mt-1 [&_strong]:font-semibold [&_table]:mt-2 [&_table]:w-full [&_table]:border-collapse [&_th]:border [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:text-[11px] [&_th]:uppercase [&_th]:text-muted [&_td]:border [&_td]:border-border [&_td]:px-2 [&_td]:py-1 [&_code]:rounded [&_code]:bg-bg [&_code]:px-1 [&_code]:text-[12.5px] [&_a]:text-accent [&_a]:underline">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

function ProgressStack({ events }: { events: string[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [events.length]);
  if (!events.length) return null;
  return (
    <div className="max-h-52 overflow-y-auto rounded-md border border-accent/25 bg-accent-soft/40 px-3 py-2">
      {events.map((e, i) => {
        const isLast = i === events.length - 1;
        const isPhase = e.startsWith("‣");
        return (
          <div
            key={i}
            className={`flex items-center gap-2 py-0.5 text-[12px] ${
              isPhase ? "mt-1 font-semibold text-accent first:mt-0" : isLast ? "text-text" : "text-muted"
            }`}
          >
            {isLast && <span className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent" />}
            {!isLast && !isPhase && <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-border" />}
            <span className="truncate">{isPhase ? e.slice(1).trim() : e}</span>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}

export function AiAnalyst() {
  const qc = useQueryClient();
  const last = useQuery({ queryKey: ["ai", "last"], queryFn: () => api<LastAnalysis>("/ai/last") });

  const [running, setRunning] = useState(false);
  const [liveText, setLiveText] = useState<string | null>(null);
  const [progress, setProgress] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [chatProgress, setChatProgress] = useState<string[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [chat, chatStreaming]);

  const runAnalysis = async () => {
    setRunning(true);
    setLiveText("");
    setProgress([]);
    setError(null);
    let text = "";
    try {
      await streamSse("/api/ai/analyze", null, (e) => {
        if (e.type === "text") {
          text += e.delta ?? "";
          setLiveText(text);
        } else if (e.type === "progress" && e.kind === "phase" && e.detail) {
          setProgress((p) => [...p, `‣ ${e.detail}`]);
        } else if (e.type === "progress" && e.kind === "search" && e.detail) {
          setProgress((p) => [...p, `🔍 Searching: ${e.detail}`]);
        } else if (e.type === "progress" && e.kind === "results") {
          setProgress((p) => [...p, `📖 Reading ${e.detail}`]);
        } else if (e.type === "phase" && e.detail === "thinking") {
          setProgress((p) => (p[p.length - 1] === "🤔 Thinking…" ? p : [...p, "🤔 Thinking…"]));
        } else if (e.type === "error") {
          setError(e.message ?? "unknown error");
        }
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setRunning(false);
      qc.invalidateQueries({ queryKey: ["ai", "last"] });
    }
  };

  const sendChat = async () => {
    const q = chatInput.trim();
    if (!q || chatStreaming) return;
    setChatInput("");
    setError(null);
    const history: ChatMessage[] = [...chat, { role: "user", content: q }];
    setChat([...history, { role: "assistant", content: "" }]);
    setChatStreaming(true);
    setChatProgress([]);
    let answer = "";
    try {
      await streamSse("/api/ai/chat", { messages: history }, (e) => {
        if (e.type === "text") {
          answer += e.delta ?? "";
          setChat([...history, { role: "assistant", content: answer }]);
        } else if (e.type === "progress" && e.kind === "search" && e.detail) {
          setChatProgress((p) => [...p, `🔍 ${e.detail}`]);
        } else if (e.type === "error") {
          setError(e.message ?? "unknown error");
        }
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setChatStreaming(false);
      setChatProgress([]);
    }
  };

  const analysisText = liveText ?? last.data?.analysis?.text ?? null;
  const meta = last.data?.analysis;

  return (
    <section className="card overflow-hidden">
      <div className="flex items-center gap-3 border-b border-border px-3 py-2">
        <h2 className="text-[13px] font-medium">AI Analyst</h2>
        <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent">
          Claude Fable
        </span>
        {meta && !running && (
          <span className="text-[11px] text-muted">last run {fmtRelTime(meta.created_at)}</span>
        )}
        <button
          onClick={runAnalysis}
          disabled={running}
          className="ml-auto rounded-md bg-accent px-3 py-1.5 text-[13px] font-medium text-white hover:bg-accent-deep disabled:opacity-50"
        >
          {running ? "Analyzing…" : analysisText ? "Re-run analysis" : "Run analysis"}
        </button>
      </div>

      <div className="p-4">
        {error && (
          <p className="mb-3 rounded-md border border-down/30 bg-down/5 px-3 py-2 text-[12.5px] text-down">{error}</p>
        )}
        {running && <ProgressStack events={progress} />}

        {analysisText ? (
          <div className="mt-2">
            <Markdown text={analysisText} />
            {running && <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-accent align-text-bottom" />}
          </div>
        ) : !running ? (
          <p className="py-6 text-center text-[13px] text-muted">
            Run analysis — Claude drafts a desk-note briefing from your live portfolio and researched
            catalysts, a devil's-advocate pass red-teams it, then the refined final report streams here.
            Takes 2–5 minutes.
          </p>
        ) : null}

        {(analysisText || chat.length > 0) && (
          <div className="mt-4 border-t border-border pt-3">
            {chat.length > 0 && (
              <div className="mb-3 max-h-[420px] space-y-3 overflow-y-auto pr-1">
                {chat.map((m, i) =>
                  m.role === "user" ? (
                    <div key={i} className="ml-auto w-fit max-w-[85%] rounded-lg bg-accent-soft px-3 py-2 text-[13px]">
                      {m.content}
                    </div>
                  ) : (
                    <div key={i} className="max-w-[95%]">
                      {m.content ? (
                        <Markdown text={m.content} />
                      ) : (
                        <ProgressStack events={chatProgress.length ? chatProgress : ["🤔 Thinking…"]} />
                      )}
                    </div>
                  ),
                )}
                <div ref={chatEndRef} />
              </div>
            )}
            <div className="flex gap-2">
              <input
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && sendChat()}
                placeholder="포트폴리오에 대해 물어보세요 — e.g. RDDT 실적 언제야? SOXL 비중 줄여야 하나?"
                disabled={chatStreaming}
                className="flex-1 rounded-md border border-border bg-panel px-3 py-2 text-[13px] placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:opacity-60"
              />
              <button
                onClick={sendChat}
                disabled={chatStreaming || !chatInput.trim()}
                className="rounded-md border border-border bg-panel px-4 py-2 text-[13px] hover:bg-bg disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
