import { useMutation, useQuery, useQueryClient, UseMutationOptions, UseQueryOptions } from "@tanstack/react-query";
import {
  createCustomer,
  createCustomerNote,
  CustomerActivityResponse,
  CustomerCreateRequest,
  CustomerDetailInclude,
  CustomerDetailResponse,
  CustomerNote,
  CustomerNotesResponse,
  CustomersListParams,
  CustomersListResponse,
  getCustomerDetail,
  importCustomers,
  CustomerImportRequest,
  CustomerImportResult,
  listCustomerActivity,
  listCustomerNotes,
  listCustomers,
  updateCustomer,
  updateCustomerNote,
  CustomerNoteCreateRequest,
  CustomerNoteUpdateRequest,
  CustomerUpdateRequest,
} from "@/services/customers";

type UUID = string;

type QueryResult<T> = {
  data: T | undefined;
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: unknown;
  isSuccess: boolean;
};

type CustomerListQueryOptions = Omit<
  UseQueryOptions<CustomersListResponse, unknown, CustomersListResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

type CustomerDetailQueryOptions = Omit<
  UseQueryOptions<CustomerDetailResponse, unknown, CustomerDetailResponse, readonly unknown[]>,
  "queryKey" | "queryFn"
>;

type CursorParams = { limit?: number; cursor?: string | null };

export const customerKeys = {
  all: ["customers"] as const,
  list: (params?: CustomersListParams) => ["customers", "list", params] as const,
  detail: (id: UUID) => ["customers", "detail", id] as const,
  notes: (id: UUID, params?: CursorParams) => ["customers", "detail", id, "notes", params] as const,
  activity: (id: UUID, params?: CursorParams) => ["customers", "detail", id, "activity", params] as const,
};

export const useCustomers = (
  params?: CustomersListParams,
  options?: CustomerListQueryOptions,
): QueryResult<CustomersListResponse> => {
  const query = useQuery<CustomersListResponse>({
    queryKey: customerKeys.list(params),
    queryFn: () => listCustomers(params),
    staleTime: 30_000,
    retry: options?.retry ?? false,
    ...options,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    isSuccess: query.isSuccess,
  };
};

export const useCustomerDetail = (
  customerId: UUID | null,
  include?: CustomerDetailInclude,
  options?: CustomerDetailQueryOptions,
): QueryResult<CustomerDetailResponse> => {
  const includeKey = include ? [...include].sort() : [];
  const queryKey = ["customers", "detail", customerId, includeKey] as const;
  const query = useQuery<CustomerDetailResponse>({
    queryKey,
    enabled: Boolean(customerId),
    queryFn: () => {
      if (!customerId) throw new Error("Missing customer id");
      return getCustomerDetail(customerId, include);
    },
    staleTime: 15_000,
    retry: options?.retry ?? false,
    ...options,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    isSuccess: query.isSuccess,
  };
};

export const useCustomerNotes = (
  customerId: UUID | null,
  params?: CursorParams,
  options?: UseQueryOptions<CustomerNotesResponse, unknown, CustomerNotesResponse, readonly unknown[]>,
): QueryResult<CustomerNotesResponse> => {
  const queryKey = ["customers", "detail", customerId, "notes", params] as const;
  const query = useQuery<CustomerNotesResponse>({
    queryKey,
    enabled: Boolean(customerId),
    queryFn: () => {
      if (!customerId) throw new Error("Missing customer id");
      return listCustomerNotes(customerId, params);
    },
    retry: options?.retry ?? false,
    ...options,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    isSuccess: query.isSuccess,
  };
};

export const useCustomerActivity = (
  customerId: UUID | null,
  params?: CursorParams,
  options?: UseQueryOptions<CustomerActivityResponse, unknown, CustomerActivityResponse, readonly unknown[]>,
): QueryResult<CustomerActivityResponse> => {
  const queryKey = ["customers", "detail", customerId, "activity", params] as const;
  const query = useQuery<CustomerActivityResponse>({
    queryKey,
    enabled: Boolean(customerId),
    queryFn: () => {
      if (!customerId) throw new Error("Missing customer id");
      return listCustomerActivity(customerId, params);
    },
    retry: options?.retry ?? false,
    ...options,
  });

  return {
    data: query.data,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    isSuccess: query.isSuccess,
  };
};

export const useCreateCustomer = (
  options?: UseMutationOptions<CustomerDetailResponse, unknown, CustomerCreateRequest>
) => {
  const queryClient = useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return useMutation<CustomerDetailResponse, unknown, CustomerCreateRequest>({
    mutationFn: (payload) => createCustomer(payload),
    onSuccess: (result, variables, context) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.all });
      onSuccess?.(result, variables, context);
    },
    ...rest,
  });
};

export const useUpdateCustomer = (
  customerId: UUID,
  options?: UseMutationOptions<CustomerDetailResponse, unknown, CustomerUpdateRequest>
) => {
  const queryClient = useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return useMutation<CustomerDetailResponse, unknown, CustomerUpdateRequest>({
    mutationFn: (payload) => updateCustomer(customerId, payload),
    onSuccess: (result, variables, context) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.detail(customerId) });
      queryClient.invalidateQueries({ queryKey: customerKeys.all });
      onSuccess?.(result, variables, context);
    },
    ...rest,
  });
};

export const useCreateCustomerNote = (
  customerId: UUID,
  options?: UseMutationOptions<CustomerNote, unknown, CustomerNoteCreateRequest>
) => {
  const queryClient = useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return useMutation<CustomerNote, unknown, CustomerNoteCreateRequest>({
    mutationFn: (payload) => createCustomerNote(customerId, payload),
    onSuccess: (result, variables, context) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.notes(customerId) });
      queryClient.invalidateQueries({ queryKey: customerKeys.detail(customerId) });
      onSuccess?.(result, variables, context);
    },
    ...rest,
  });
};

export const useUpdateCustomerNote = (
  customerId: UUID,
  options?: UseMutationOptions<CustomerNote, unknown, { noteId: UUID; payload: CustomerNoteUpdateRequest }>
) => {
  const queryClient = useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return useMutation<CustomerNote, unknown, { noteId: UUID; payload: CustomerNoteUpdateRequest }>({
    mutationFn: ({ noteId, payload }) => updateCustomerNote(customerId, noteId, payload),
    onSuccess: (result, variables, context) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.notes(customerId) });
      queryClient.invalidateQueries({ queryKey: customerKeys.detail(customerId) });
      onSuccess?.(result, variables, context);
    },
    ...rest,
  });
};

export const useImportCustomers = (
  options?: UseMutationOptions<CustomerImportResult, unknown, CustomerImportRequest>
) => {
  const queryClient = useQueryClient();
  const { onSuccess, ...rest } = options ?? {};
  return useMutation<CustomerImportResult, unknown, CustomerImportRequest>({
    mutationFn: (payload) => importCustomers(payload),
    onSuccess: (result, variables, context) => {
      queryClient.invalidateQueries({ queryKey: customerKeys.all });
      onSuccess?.(result, variables, context);
    },
    ...rest,
  });
};
