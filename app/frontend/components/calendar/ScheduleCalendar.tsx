import interactionPlugin from "@fullcalendar/interaction";
import dayGridPlugin from "@fullcalendar/daygrid";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";

export type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  className?: string;
  display?: "background";
  backgroundColor?: string;
  textColor?: string;
  borderColor?: string;
  extendedProps?: Record<string, unknown>;
};

type Props = {
  events: CalendarEvent[];
  workdayStartMinute: number;
  workdayEndMinute: number;
  workingDays?: number[];
  onDateClick: (dateKey: string, startStr: string, pointer: { x: number; y: number }) => void;
  onEventClick: (eventId: string, startStr: string, pointer: { x: number; y: number }, target: HTMLElement) => void;
};

function minuteToFullCalendarTime(minute: number): string {
  const clamped = Math.max(0, Math.min(minute, 1440));
  const hours = Math.floor(clamped / 60);
  const minutes = clamped % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:00`;
}

export function ScheduleCalendar({ events, workdayStartMinute, workdayEndMinute, workingDays, onDateClick, onEventClick }: Props) {
  const slotMinTime = minuteToFullCalendarTime(workdayStartMinute);
  const slotMaxTime = minuteToFullCalendarTime(workdayEndMinute);
  const scrollTime = minuteToFullCalendarTime(Math.min(workdayStartMinute + 60, workdayEndMinute - 30));
  const isMobile = typeof window !== "undefined" && window.innerWidth < 640;
  const hiddenDays = isMobile && workingDays?.length
    ? [0, 1, 2, 3, 4, 5, 6].filter((d) => !workingDays.includes(d))
    : [];

  return (
    <FullCalendar
      plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
      initialView="timeGridWeek"
      headerToolbar={{
        left: "prev,next today",
        center: "title",
        right: "timeGridWeek,dayGridMonth",
      }}
      buttonText={{
        today: "Today",
        week: "Week",
        month: "Month",
      }}
      firstDay={1}
      views={{
        timeGridWeek: {
          dayHeaderFormat: { weekday: "short", month: "numeric", day: "numeric", omitCommas: true },
        },
        dayGridMonth: {
          dayHeaderFormat: { weekday: "short" },
        },
      }}
      slotLabelFormat={{ hour: "numeric", minute: "2-digit", meridiem: "short" }}
      events={events}
      hiddenDays={hiddenDays}
      editable={false}
      allDaySlot={false}
      nowIndicator
      stickyHeaderDates
      expandRows
      slotMinTime={slotMinTime}
      slotMaxTime={slotMaxTime}
      scrollTime={scrollTime}
      slotDuration="00:30:00"
      slotLabelInterval="01:00:00"
      height="auto"
      dateClick={(info) =>
        onDateClick(info.dateStr.slice(0, 10), info.dateStr, { x: info.jsEvent.clientX, y: info.jsEvent.clientY })
      }
      eventContent={(arg) => {
        if (arg.event.display === "background") return undefined;
        const patientId = arg.event.extendedProps?.patientId as number | undefined;
        return (
          <div className="fc-event-main-inner">
            {arg.timeText && <div className="fc-event-time">{arg.timeText}</div>}
            <div className="fc-event-title">
              {patientId != null ? (
                <span className="rc-event-patient-name" data-patient-id={patientId}>{arg.event.title}</span>
              ) : arg.event.title}
            </div>
          </div>
        );
      }}
      eventClick={(info) => onEventClick(info.event.id, info.event.startStr, { x: info.jsEvent.clientX, y: info.jsEvent.clientY }, info.jsEvent.target as HTMLElement)}
    />
  );
}
