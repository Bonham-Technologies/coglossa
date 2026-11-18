import { useMutation, useQuery } from "@tanstack/react-query";
import { client, unwrap } from "./client";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => unwrap(client.GET("/api/me")) });
}

export function useDoctor() {
  return useMutation({
    mutationFn: (probe: boolean) =>
      unwrap(client.GET("/api/system/doctor", { params: { query: { probe } } })),
  });
}

export function useConfig() {
  return useQuery({
    queryKey: ["system", "config"],
    queryFn: () => unwrap(client.GET("/api/system/config")),
  });
}

export function useTiers() {
  return useQuery({
    queryKey: ["system", "tiers"],
    queryFn: () => unwrap(client.GET("/api/system/tiers")),
  });
}

/** The quick installation check, loaded when Settings opens (spec §6.2). */
export function useDoctorStatus() {
  return useQuery({
    queryKey: ["system", "doctor"],
    queryFn: () =>
      unwrap(client.GET("/api/system/doctor", { params: { query: { probe: false } } })),
  });
}

/** CGL-26: team names to suggest when moving a set (admins only). */
export function useTeams(enabled: boolean) {
  return useQuery({
    queryKey: ["teams"],
    queryFn: () => unwrap(client.GET("/api/teams")),
    enabled,
  });
}
