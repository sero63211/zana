import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addRuntime,
  deleteRuntime,
  fetchModel,
  fetchModels,
  fetchRuntimes,
  fetchSystemDoctor,
  fetchSystemProfile,
  pullModel,
  refreshRuntimes,
} from "../api/client";
import type { ModelFilters, ModelPullPayload, RuntimeCreatePayload } from "../api/types";

export const SYSTEM_PROFILE_QUERY_KEY = ["system", "profile"] as const;
export const SYSTEM_DOCTOR_QUERY_KEY = ["system", "doctor"] as const;
export const RUNTIMES_QUERY_KEY = ["runtimes"] as const;
export const MODELS_QUERY_KEY = ["models"] as const;

type SignalVariables = { signal?: AbortSignal };

export function useSystemProfile() {
  return useQuery({
    queryKey: SYSTEM_PROFILE_QUERY_KEY,
    queryFn: ({ signal }) => fetchSystemProfile(signal),
    retry: false,
  });
}

export function useSystemDoctor() {
  return useQuery({
    queryKey: SYSTEM_DOCTOR_QUERY_KEY,
    queryFn: ({ signal }) => fetchSystemDoctor(signal),
    retry: false,
  });
}

export function useRuntimes() {
  return useQuery({
    queryKey: RUNTIMES_QUERY_KEY,
    queryFn: ({ signal }) => fetchRuntimes(signal),
    retry: false,
  });
}

export function useModels(filters: ModelFilters = {}) {
  return useQuery({
    queryKey: [...MODELS_QUERY_KEY, filters] as const,
    queryFn: ({ signal }) => fetchModels(filters, signal),
    retry: false,
  });
}

export function useModel(modelKey: string | null) {
  return useQuery({
    queryKey: [...MODELS_QUERY_KEY, "detail", modelKey] as const,
    queryFn: ({ signal }) => fetchModel(modelKey ?? "", signal),
    enabled: modelKey !== null && modelKey !== "",
    retry: false,
  });
}

export function useRefreshRuntimes() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ signal }: SignalVariables) => refreshRuntimes(signal),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: RUNTIMES_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: MODELS_QUERY_KEY });
    },
  });
}

export function useAddRuntime() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ payload, signal }: { payload: RuntimeCreatePayload } & SignalVariables) =>
      addRuntime(payload, signal),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: RUNTIMES_QUERY_KEY });
    },
  });
}

export function useDeleteRuntime() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runtimeId, signal }: { runtimeId: number } & SignalVariables) =>
      deleteRuntime(runtimeId, signal),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: RUNTIMES_QUERY_KEY });
      void queryClient.invalidateQueries({ queryKey: MODELS_QUERY_KEY });
    },
  });
}

export function usePullModel() {
  return useMutation({
    mutationFn: ({ payload, signal }: { payload: ModelPullPayload } & SignalVariables) =>
      pullModel(payload, signal),
  });
}
