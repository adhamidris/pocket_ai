import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { MessageCircle, X, Send, Bot, User } from "lucide-react";
import { useI18n } from "@/i18n/I18nProvider";

interface Message {
  id: number;
  text: string;
  isBot: boolean;
  timestamp: Date;
}

const ChatWidget = () => {
  const { t, dir } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 1,
      text: t("chat.initial"),
      isBot: true,
      timestamp: new Date(),
    },
  ]);
  const [inputValue, setInputValue] = useState("");
  const [isTyping, setIsTyping] = useState(false);

  const demoResponses: string[] = t<string[]>("chat.demoResponses");

  const handleSendMessage = () => {
    if (!inputValue.trim()) return;

    const userMessage: Message = {
      id: messages.length + 1,
      text: inputValue,
      isBot: false,
      timestamp: new Date(),
    };

    setMessages(prev => [...prev, userMessage]);
    setInputValue("");
    setIsTyping(true);

    // Simulate AI response
    setTimeout(() => {
      const botMessage: Message = {
        id: messages.length + 2,
        text: demoResponses[Math.floor(Math.random() * demoResponses.length)],
        isBot: true,
        timestamp: new Date(),
      };
      setMessages(prev => [...prev, botMessage]);
      setIsTyping(false);
    }, 1500);
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="relative">
      {/* Chat Widget Button */}
      <Button
        onClick={() => setIsOpen(!isOpen)}
        className={`
          chat-fab fixed bottom-6 right-6 w-14 h-14 rounded-full shadow-lg transition-all duration-300 z-50
          ${isOpen 
            ? "bg-neutral-100 text-neutral-600 hover:bg-neutral-200" 
            : "bg-gradient-primary text-white hover:scale-110 hover:shadow-xl"
          }
        `}
      >
        {isOpen ? <X size={24} /> : <MessageCircle size={24} />}
      </Button>

      {/* Chat Window */}
      {isOpen && (
        <div className="chat-window fixed bottom-24 right-6 w-80 h-96 bg-card rounded-2xl shadow-premium border border-border flex flex-col z-40 animate-scale-in overflow-hidden">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-border bg-secondary rounded-t-2xl">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full flex items-center justify-center bg-primary/10">
                <Bot size={16} className="text-primary" />
              </div>
              <div>
                <div className="font-medium text-card-foreground">{t("chat.headerName")}</div>
                <div className="text-xs text-muted-foreground flex items-center gap-1">
                  <div className="w-2 h-2 bg-green-400 rounded-full" />
                  {t("chat.online")}
                </div>
              </div>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 p-4 overflow-y-auto no-scrollbar space-y-4 bg-card">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex ${message.isBot ? "justify-start" : "justify-end"}`}
              >
                <div
                  className={`
                    max-w-xs px-4 py-2 rounded-2xl text-sm
                    ${message.isBot
                      ? "bg-card text-card-foreground border border-border/50"
                      : "bg-gradient-primary text-white"
                    }
                  `}
                >
                  {message.text}
                </div>
              </div>
            ))}
            
            {/* Typing indicator */}
            {isTyping && (
              <div className="flex justify-start">
                <div className="bg-card px-4 py-2 rounded-2xl border border-border/50">
                  <div className="flex gap-1">
                    <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" />
                    <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }} />
                    <div className="w-2 h-2 bg-neutral-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }} />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Input */}
          <div className="p-4 border-t border-border bg-card/50">
            <div className="flex gap-2">
              <input
                type="text"
                placeholder={t("chat.inputPlaceholder")}
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyPress={handleKeyPress}
                className="flex-1 px-3 py-2 bg-input border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary text-sm text-foreground placeholder:text-muted-foreground"
              />
              <Button
                onClick={handleSendMessage}
                size="sm"
                className="bg-primary text-primary-foreground hover:opacity-90 px-3"
              >
                <Send size={16} />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ChatWidget;
