import interactionPlugin from "@fullcalendar/interaction";
import dayGridPlugin from "@fullcalendar/daygrid";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";

type CalendarEvent = {
  id: string;
  title: string;
  start: string;
  end: string;
  className: string;
  display?: "background";
};

type Props = {
  events: CalendarEvent[];
  workdayStartMinute: number;
  workdayEndMinute: number;
  onDateClick: (dateKey: string, startStr: string, pointer: { x: number; y: number }) => void;
  onEventClick: (eventId: string, startStr: string, pointer: { x: number; y: number }) => void;
};

function minuteToFullCalendarTime(minute: number): string {
  const clamped = Math.max(0, Math.min(minute, 1440));
  const hours = Math.floor(clamped / 60);
  const minutes = clamped % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:00`;
}

export function ScheduleCalendar({ events, workdayStartMinute, workdayEndMinute, onDateClick, onEventClick }: Props) {
  const slotMinTime = minuteToFullCalendarTime(workdayStartMinute);
  const slotMaxTime = minuteToFullCalendarTime(workdayEndMinute);
  const scrollTime = minuteToFullCalendarTime(Math.min(workdayStartMinute + 60, workdayEndMinute - 30));

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
      dayHeaderFormat={{ weekday: "short", month: "numeric", day: "numeric", omitCommas: true }}
      slotLabelFormat={{ hour: "numeric", minute: "2-digit", meridiem: "short" }}
      events={events}
      editable={false}
      selectable
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
      eventClick={(info) => onEventClick(info.event.id, info.event.startStr, { x: info.jsEvent.clientX, y: info.jsEvent.clientY })}
    />
  );
}
