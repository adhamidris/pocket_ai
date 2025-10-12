import { useMutation, UseMutationOptions, useQueryClient } from "@tanstack/react-query";
import {
  ChatMessageSendRequest,
  ChatMessageSendResponse,
  sendChatMessage,
} from "@/services/chat";
import { chatKeys } from "./useChatSession";

type MutationOptions = UseMutationOptions<ChatMessageSendResponse, unknown, ChatMessageSendRequest, unknown>;

export const useSendChatMessage = (options?: MutationOptions) => {
  const queryClient = useQueryClient();

  return useMutation<ChatMessageSendResponse, unknown, ChatMessageSendRequest>({
    mutationFn: async (variables) => sendChatMessage(variables),
    onSuccess: (data, variables, context) => {
      const baseKey = chatKeys.messages(variables.sessionToken ?? null);
      queryClient.invalidateQueries({ queryKey: baseKey, exact: false }).catch(() => {
        /* ignore */
      });
      options?.onSuccess?.(data, variables, context);
    },
    ...options,
  });
};

export type SendChatMessageMutationResult = ReturnType<typeof useSendChatMessage>;
