import React, { useEffect, useMemo, useRef, useState } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import ChatMessage, { Message } from "./ChatMessage";
import { Loader2 } from "lucide-react";

interface ChatMessagesProps {
  messages: Message[];
  isLoading?: boolean;
}

const ChatMessages: React.FC<ChatMessagesProps> = ({ messages, isLoading = false }) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [showJump, setShowJump] = useState(false);

  // Auto-scroll with near-bottom detection
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [messages]);

  // Group consecutive messages and day separators
  const grouped = useMemo(() => {
    const result: Array<
      | { type: 'day'; key: string; label: string }
      | { type: 'msg'; key: string; message: Message; groupedWithPrev: boolean }
    > = [];
    let lastDay: string | null = null;
    let lastSender: string | null = null;
    messages.forEach((m, idx) => {
      const dayKey = m.timestamp.toDateString();
      if (dayKey !== lastDay) {
        result.push({ type: 'day', key: `day-${dayKey}`, label: dayKey });
        lastDay = dayKey;
        lastSender = null;
      }
      const groupedWithPrev = lastSender === m.sender && m.sender !== 'system';
      result.push({ type: 'msg', key: m.id, message: m, groupedWithPrev });
      lastSender = m.sender;
    });
    return result;
  }, [messages]);

  return (
    <div className="flex-1 overflow-hidden bg-background">
      <ScrollArea className="h-full">
        <div
          ref={el => { scrollRef.current = el as HTMLDivElement; viewportRef.current = el as HTMLDivElement; }}
          role="log"
          aria-live="polite"
          aria-relevant="additions"
          className="px-4 md:px-6 py-6 flex flex-col gap-3 max-w-screen-lg mx-auto min-h-full"
        >
          {/* Empty state */}
          {messages.length === 0 && !isLoading && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center space-y-3 max-w-md">
                <div className="h-16 w-16 rounded-full bg-primary/10 text-primary grid place-items-center mx-auto">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-8 h-8"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"
                    />
                  </svg>
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-foreground mb-1">
                    Start the conversation
                  </h3>
                  <p className="text-sm text-muted-foreground">
                    Send a message to begin chatting with the AI agent
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Messages + Day separators */}
          {grouped.map((item) => {
            if (item.type === 'day') {
              return (
                <div key={item.key} className="flex items-center justify-center my-2">
                  <div className="px-3 py-1 text-[10px] rounded-full bg-muted/50 border border-border/60 text-muted-foreground">
                    {item.label}
                  </div>
                </div>
              );
            }
            return (
              <div key={item.key} className={item.groupedWithPrev ? "-mt-1.5" : undefined}>
                <ChatMessage message={item.message} />
              </div>
            );
          })}

          {/* Loading indicator */}
          {isLoading && (
            <div className="flex gap-3 animate-fade-in">
              <div className="h-8 w-8 shrink-0 rounded-full bg-primary/10 grid place-items-center mt-1">
                <Loader2 className="h-4 w-4 text-primary animate-spin" />
              </div>
              <div className="flex flex-col">
                <div className="px-4 py-3 bg-card border border-border/40 rounded-2xl rounded-tl-sm shadow-sm">
                  <div className="flex gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.3s]" />
                    <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce [animation-delay:-0.15s]" />
                    <span className="h-2 w-2 rounded-full bg-muted-foreground animate-bounce" />
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Scroll anchor */}
          <div ref={bottomRef} />
          {/* Jump to bottom */}
          {showJump && (
            <button
              type="button"
              onClick={() => viewportRef.current?.scrollTo({ top: viewportRef.current.scrollHeight, behavior: 'smooth' })}
              className="fixed bottom-24 right-5 rounded-full px-3 py-1.5 text-xs bg-primary text-primary-foreground shadow-md hover:opacity-90"
              aria-label="Jump to latest messages"
            >
              New messages
            </button>
          )}
        </div>
      </ScrollArea>
    </div>
  );
};

export default ChatMessages;

