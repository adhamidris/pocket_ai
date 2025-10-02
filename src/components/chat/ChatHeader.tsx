import React from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import {
  MoreVertical,
  Download,
  Trash2,
  Flag,
  LogOut,
} from "lucide-react";

interface ChatHeaderProps {
  agentName: string;
  agentRole: string;
  agentAvatar?: string;
  isOnline: boolean;
  isTyping: boolean;
  onClearChat?: () => void;
  onDownloadTranscript?: () => void;
  onReportIssue?: () => void;
  onEndSession?: () => void;
}

const ChatHeader: React.FC<ChatHeaderProps> = ({
  agentName,
  agentRole,
  agentAvatar,
  isOnline,
  isTyping,
  onClearChat,
  onDownloadTranscript,
  onReportIssue,
  onEndSession,
}) => {
  return (
    <header className="sticky top-0 z-50 bg-background border-b border-border/60 relative">
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-primary/35 via-primary/20 to-transparent" />
      <div className="px-4 md:pl-6 md:pr-6 py-4">
        <div className="flex items-start justify-between">
          {/* Left section - Agent info */}
          <div className="flex items-center gap-3 min-w-0">
            {/* Avatar with online indicator */}
            <div className="relative shrink-0">
              <Avatar className="h-10 w-10">
                <AvatarImage src={agentAvatar} alt={agentName} />
                <AvatarFallback className="bg-primary/10 text-primary font-semibold">
                  {agentName.charAt(0)}
                </AvatarFallback>
              </Avatar>
              {/* Online status dot */}
              {isOnline && (
                <span className="absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full bg-success border-2 border-card">
                  <span className="absolute inset-0 rounded-full bg-success animate-ping opacity-75" />
                </span>
              )}
            </div>

            {/* Agent details */}
            <div className="min-w-0 flex-1">
              <div className="font-semibold text-foreground truncate flex items-center gap-2">
                {agentName}
                {isOnline && (
                  <span className="text-[10px] font-medium text-success uppercase tracking-wide">
                    • Online
                  </span>
                )}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {isTyping ? (
                  <span className="flex items-center gap-1 typing-indicator">
                    Typing
                    <span className="typing-dots">
                      <span className="typing-dot">.</span>
                      <span className="typing-dot">.</span>
                      <span className="typing-dot">.</span>
                    </span>
                  </span>
                ) : (
                  agentRole
                )}
              </div>
            </div>
          </div>

          {/* Right section - Menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0 h-9 w-9 hover:bg-accent/40"
                aria-label="Chat options"
              >
                <MoreVertical className="h-5 w-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem onClick={onDownloadTranscript}>
                <Download className="mr-2 h-4 w-4" />
                <span>Download transcript</span>
              </DropdownMenuItem>
              <DropdownMenuItem onClick={onClearChat} className="text-amber-600 dark:text-amber-400">
                <Trash2 className="mr-2 h-4 w-4" />
                <span>Clear chat</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onReportIssue}>
                <Flag className="mr-2 h-4 w-4" />
                <span>Report issue</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={onEndSession} className="text-destructive">
                <LogOut className="mr-2 h-4 w-4" />
                <span>End session</span>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
};

export default ChatHeader;

