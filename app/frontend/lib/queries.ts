import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  Alert,
  CalendarBlock,
  CalendarConnection,
  ClinicianProfile,
  Message,
  Patient,
  WeeklySchedule,
} from "../types";

export const queryKeys = {
  alerts: ["alerts"] as const,
  schedule: ["schedule"] as const,
  patients: ["patients"] as const,
  messages: ["messages"] as const,
  calendarBlocks: ["calendar_blocks"] as const,
  calendarConnections: ["calendar_connections"] as const,
  clinicianProfile: ["clinician_profile"] as const,
};

export function useAlertsQuery() {
  return useQuery<Alert[]>({
    queryKey: queryKeys.alerts,
    queryFn: async () => {
      const { alerts } = await api.listAlerts();
      return alerts;
    },
    refetchOnWindowFocus: true,
  });
}

export function useScheduleQuery() {
  return useQuery<WeeklySchedule | null>({
    queryKey: queryKeys.schedule,
    queryFn: async () => {
      const { schedule } = await api.getSchedule();
      return schedule;
    },
    refetchOnWindowFocus: true,
  });
}

export function usePatientsQuery() {
  return useQuery<Patient[]>({
    queryKey: queryKeys.patients,
    queryFn: async () => {
      const { patients } = await api.listPatients();
      return patients;
    },
  });
}

export function useMessagesQuery() {
  return useQuery<Message[]>({
    queryKey: queryKeys.messages,
    queryFn: async () => {
      const { messages } = await api.listMessages();
      return messages;
    },
    refetchOnWindowFocus: true,
  });
}

export function useCalendarBlocksQuery() {
  return useQuery<CalendarBlock[]>({
    queryKey: queryKeys.calendarBlocks,
    queryFn: async () => {
      const { calendar_blocks } = await api.listCalendarBlocks();
      return calendar_blocks;
    },
  });
}

export function useCalendarConnectionsQuery() {
  return useQuery<CalendarConnection[]>({
    queryKey: queryKeys.calendarConnections,
    queryFn: async () => {
      const { calendar_connections } = await api.listCalendarConnections();
      return calendar_connections;
    },
  });
}

export function useClinicianProfileQuery() {
  return useQuery<ClinicianProfile>({
    queryKey: queryKeys.clinicianProfile,
    queryFn: async () => {
      const { clinician_profile } = await api.getClinicianProfile();
      return clinician_profile;
    },
  });
}
