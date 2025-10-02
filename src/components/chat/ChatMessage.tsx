import React from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";
import { format } from "date-fns";

export type MessageSender = "user" | "agent" | "system";

export interface Message {
  id: string;
  content: string;
  sender: MessageSender;
  timestamp: Date;
  agentName?: string;
  agentAvatar?: string;
}

interface ChatMessageProps {
  message: Message;
}

const ChatMessage: React.FC<ChatMessageProps> = ({ message }) => {
  const { content, sender, timestamp, agentName, agentAvatar } = message;

  // System messages (like "Session started", "Agent joined", etc.)
  if (sender === "system") {
    return (
      <div className="flex justify-center my-4">
        <div className="px-4 py-2 rounded-full bg-muted/50 text-muted-foreground text-xs">
          {content}
        </div>
      </div>
    );
  }

  const isAgent = sender === "agent";
  const isUser = sender === "user";

  return (
    <div
      className={cn(
        "flex gap-3 animate-fade-in w-full",
        isUser && "flex-row-reverse"
      )}
    >
      {/* Avatar - only for agent messages */}
      {isAgent && (
        <Avatar className="h-8 w-8 shrink-0 mt-1">
          <AvatarImage src={agentAvatar} alt={agentName} />
          <AvatarFallback className="bg-primary/10 text-primary text-xs">
            {agentName?.charAt(0) || "A"}
          </AvatarFallback>
        </Avatar>
      )}

      {/* Message bubble */}
      <div
        className={cn(
          "flex flex-col max-w-[75%] md:max-w-[60%]",
          isUser && "items-end"
        )}
      >
        {/* Sender name - only for agent */}
        {isAgent && agentName && (
          <span className="text-xs text-muted-foreground mb-1 ml-1">
            {agentName}
          </span>
        )}

        {/* Message content */}
        <div className="group relative w-full">
          <div
            className={cn(
              "px-4 py-3 text-sm leading-relaxed transition-all duration-200",
              isAgent &&
                "bg-card border border-border/40 rounded-2xl rounded-tl-sm shadow-sm hover:shadow-md hover:scale-[1.01]",
              isUser &&
                "bg-primary/10 text-foreground rounded-2xl rounded-tr-sm hover:bg-primary/15"
            )}
          >
            {content}
          </div>
          <button
            type="button"
            className={cn(
              "opacity-0 group-hover:opacity-100 transition-opacity text-[10px]",
              "absolute -top-2",
              isUser ? "left-0" : "right-0",
              "rounded-md px-2 py-0.5 bg-muted/70 border border-border/60 text-muted-foreground hover:text-foreground"
            )}
            aria-label="Copy message"
            onClick={() => navigator.clipboard?.writeText(content)}
          >
            Copy
          </button>
        </div>

        {/* Timestamp */}
        <span className="text-[11px] text-muted-foreground mt-1 mx-1">
          {format(timestamp, "HH:mm")}
        </span>
      </div>

      {/* Spacer for user messages to keep alignment */}
      {isUser && <div className="w-8 shrink-0" />}
    </div>
  );
};

export default ChatMessage;

