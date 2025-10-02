import React, { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Paperclip, Send } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSendMessage: (message: string) => void;
  onAttachFile?: () => void;
  onStop?: () => void;
  isLoading?: boolean;
  disabled?: boolean;
  placeholder?: string;
}

const ChatInput: React.FC<ChatInputProps> = ({
  onSendMessage,
  onAttachFile,
  onStop,
  isLoading = false,
  disabled = false,
  placeholder = "Type your message...",
}) => {
  const [message, setMessage] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      const scrollHeight = textarea.scrollHeight;
      // Limit to 4 rows (approx 96px max)
      textarea.style.height = `${Math.min(scrollHeight, 96)}px`;
    }
  }, [message]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (message.trim() && !disabled) {
      onSendMessage(message.trim());
      setMessage("");
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = "auto";
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Send on Enter (without Shift)
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const isEmpty = !message.trim();

  return (
    <footer className="fixed bottom-0 left-0 right-0 bg-muted/70 supports-[backdrop-filter]:bg-muted/70 backdrop-blur border-t border-border/60 shadow-lg relative">
      <div className="max-w-screen-lg mx-auto px-4 md:px-6 py-4 pb-[calc(env(safe-area-inset-bottom)+16px)]">
        <form onSubmit={handleSubmit} className="flex items-end gap-2">
          {/* Attachment button */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="shrink-0 h-10 w-10 hover:bg-accent/40 transition-colors"
                onClick={onAttachFile}
                disabled={disabled}
                aria-label="Attach file"
              >
                <Paperclip className="h-5 w-5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">
              <p>Attach file</p>
            </TooltipContent>
          </Tooltip>

          {/* Textarea */}
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              disabled={disabled}
              rows={1}
              className={cn(
                "w-full resize-none bg-input border border-border rounded-xl px-4 py-3",
                "text-sm leading-relaxed text-foreground placeholder:text-muted-foreground",
                "focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent",
                "transition-all duration-200",
                "disabled:opacity-50 disabled:cursor-not-allowed"
              )}
              style={{
                minHeight: "44px",
                maxHeight: "96px",
              }}
            />
          </div>

          {/* Right controls */}
          {isLoading ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onStop}
              aria-label="Stop generating"
              className="shrink-0 h-10 rounded-lg px-3"
            >
              Stop
            </Button>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="submit"
                  size="icon"
                  disabled={isEmpty || disabled}
                  className={cn(
                    "shrink-0 h-10 w-10 rounded-lg transition-all duration-200",
                    isEmpty || disabled
                      ? "opacity-50 cursor-not-allowed bg-muted"
                      : "bg-gradient-primary text-white hover:opacity-90 hover:scale-105 shadow-md"
                  )}
                  aria-label="Send message"
                >
                  <Send className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">
                <p>Send message</p>
              </TooltipContent>
            </Tooltip>
          )}
        </form>

        {/* Helpful hint */}
        <p className="text-[10px] text-muted-foreground mt-2 text-center">
          Press Enter to send • Shift + Enter for new line
        </p>
      </div>
    </footer>
  );
};

export default ChatInput;

