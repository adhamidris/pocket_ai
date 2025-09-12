import { useState, useEffect, useRef } from "react";
import { Bot, User } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";

interface DemoMessage {
  id: number;
  text: string;
  isBot: boolean;
  delay: number;
  images?: string[];
}

const DemoChatWidget = () => {
  const [messages, setMessages] = useState<DemoMessage[]>([]);
  const [currentMessageIndex, setCurrentMessageIndex] = useState(0);
  const [phase, setPhase] = useState<'idle' | 'pre-bot' | 'streaming'>('idle');
  const containerRef = useRef<HTMLDivElement | null>(null);

  type Scenario = {
    agentName: string;
    jobTitle: string;
    conversation: DemoMessage[];
  };
  const { t, dir } = useI18n();
  const scenarios: Scenario[] = t<Scenario[]>("demo.scenarios");

  const [scenarioIndex, setScenarioIndex] = useState(() => Math.floor(Math.random() * scenarios.length));
  const currentScenario = scenarios[scenarioIndex];

  useEffect(() => {
    if (currentMessageIndex >= currentScenario.conversation.length) {
      const resetTimer = setTimeout(() => {
        setMessages([]);
        setCurrentMessageIndex(0);
        setPhase('idle');
        setScenarioIndex((prev) => {
          if (scenarios.length <= 1) return prev;
          let next = Math.floor(Math.random() * scenarios.length);
          if (next === prev) next = (prev + 1) % scenarios.length;
          return next;
        });
      }, 2800);
      return () => clearTimeout(resetTimer);
    }

    const currentMessage = currentScenario.conversation[currentMessageIndex];

    if (currentMessage.isBot) {
      setPhase('pre-bot');
    } else {
      setPhase('idle');
    }

    const timer = setTimeout(() => {
      if (currentMessage.isBot) {
        if (currentMessage.images && currentMessage.images.length > 0) {
          setPhase('idle');
          setMessages(prev => [...prev, currentMessage]);
          setCurrentMessageIndex(prev => prev + 1);
          return;
        }

        setPhase('streaming');
        const fullText = currentMessage.text;
        const perCharMs = 50;
        const totalDurationMs = Math.min(2000, Math.max(700, Math.ceil(fullText.length * perCharMs)));
        const stepMs = Math.max(16, Math.floor(totalDurationMs / Math.max(1, fullText.length)));
        setMessages(prev => [...prev, { ...currentMessage, text: "" }]);
        let pos = 0;
        const streamInterval = setInterval(() => {
          pos += 1;
          const nextText = fullText.slice(0, pos);
          setMessages(prev => {
            if (prev.length === 0) return prev;
            const updated = prev.slice();
            updated[updated.length - 1] = { ...updated[updated.length - 1], text: nextText } as typeof currentMessage;
            return updated;
          });
          if (pos >= fullText.length) {
            clearInterval(streamInterval);
            setPhase('idle');
            setCurrentMessageIndex(prev => prev + 1);
          }
        }, stepMs);
        return;
      }

      setPhase('idle');
      setMessages(prev => [...prev, currentMessage]);
      setCurrentMessageIndex(prev => prev + 1);
    }, currentMessage.delay);

    return () => clearTimeout(timer);
  }, [currentMessageIndex, currentScenario]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  }, [messages, phase]);

  const lastMessageIsBot = messages.length > 0 ? messages[messages.length - 1].isBot : false;

  return (
    <div className="bg-card rounded-xl shadow-premium border border-border/50 overflow-hidden">
      {/* Chat Header */}
      <div className="flex items-center justify-between p-4 bg-secondary">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full flex items-center justify-center bg-primary/10">
            <Bot size={16} className="text-primary" />
          </div>
          <div>
            <div className="font-medium text-card-foreground">{currentScenario.agentName}</div>
            <div className="text-[11px] text-muted-foreground">{currentScenario.jobTitle}</div>
            <div className="text-[11px] text-muted-foreground flex items-center gap-1 mt-0.5">
              <span className="w-2 h-2 bg-success-light rounded-full animate-pulse" />
              {t("demo.online")}
            </div>
          </div>
        </div>
      </div>

      {/* Chat Messages */}
      <div ref={containerRef} className="h-80 p-4 overflow-y-auto no-scrollbar overscroll-contain space-y-3 bg-card">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex animate-fade-in ${message.isBot ? "justify-start" : "justify-end"}`}
          >
            <div className={`flex items-start gap-2 max-w-[80%] ${message.isBot ? "" : "flex-row-reverse"}`}>
              <div className={`
                w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-1
                ${message.isBot ? "bg-primary/20" : "bg-neutral-300"}
              `}>
                {message.isBot ? (
                  <Bot size={12} className="text-primary" />
                ) : (
                  <User size={12} className="text-neutral-600" />
                )}
              </div>
              <div
                className={`
                  px-4 py-2 rounded-2xl text-sm shadow-sm
                  ${message.isBot
                    ? "bg-card text-card-foreground border border-border/50"
                    : "bg-gradient-primary text-white"
                  }
                  ${message.isBot ? (dir === 'rtl' ? 'rounded-br-sm' : 'rounded-bl-sm') : (dir === 'rtl' ? 'rounded-bl-sm' : 'rounded-br-sm')}
                `}
              >
                {message.text}
                {message.images && message.images.length > 0 && (
                  <div className="mt-2 grid grid-cols-3 gap-2">
                    {message.images.map((src, idx) => (
                      <img
                        key={idx}
                        src={src}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        draggable={false}
                        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
                        className="w-24 h-16 object-cover rounded-md"
                        aria-hidden
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
        
        {/* Typing indicator */}
        {phase === 'pre-bot' && !lastMessageIsBot && (
          <div className="flex justify-start animate-fade-in">
            <div className="flex items-start gap-2">
              <div className="w-6 h-6 rounded-full bg-primary/20 flex items-center justify-center mt-1">
                <Bot size={12} className="text-primary" />
              </div>
              <div className="bg-card px-4 py-3 rounded-2xl rounded-bl-sm border border-border/50">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" />
                  <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" style={{ animationDelay: '0.15s' }} />
                  <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" style={{ animationDelay: '0.3s' }} />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Chat Input (disabled for demo) */}
          <div className="p-4 border-t border-border/50 bg-card/50">
        <div className="flex gap-2 opacity-50">
          <input
            type="text"
            placeholder={t("demo.inputPlaceholder")}
            disabled
            className="flex-1 px-3 py-2 bg-input border border-border rounded-lg text-sm text-muted-foreground"
          />
          <div className="w-10 h-9 bg-primary/20 rounded-lg flex items-center justify-center">
            <Bot size={16} className="text-primary/50" />
          </div>
        </div>
      </div>
    </div>
  );
};

export default DemoChatWidget;
