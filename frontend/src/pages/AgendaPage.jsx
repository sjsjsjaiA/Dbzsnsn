import React, { useState, useEffect, useCallback, useRef } from "react";
import { useAmbulatorio, apiClient } from "@/App";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Calendar } from "@/components/ui/calendar";
import { format, addDays, subDays, isWeekend } from "date-fns";
import { it } from "date-fns/locale";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  CalendarIcon,
  Search,
  X,
  Syringe,
  Bandage,
  Droplets,
  CircleDot,
  UserPlus,
  ExternalLink,
  Lock,
  Unlock,
  Ban,
  RefreshCw,
  FileSpreadsheet,
} from "lucide-react";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { Badge } from "@/components/ui/badge";

const TIME_SLOTS = [
  "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00",
  "15:00", "15:30", "16:00", "16:30", "17:00"
];

// Funzione per ottenere classe colore in base allo stato
const getStatoColorClass = (stato) => {
  switch (stato) {
    case "effettuato":
      return "bg-green-500 text-white border-green-600";
    case "non_presentato":
      return "bg-red-500 text-white border-red-600";
    default: // da_fare
      return "bg-slate-800 text-white border-slate-900";
  }
};

const PRESTAZIONI_PICC = [
  { id: "medicazione_semplice", label: "Medicazione semplice", icon: Bandage },
  { id: "irrigazione_catetere", label: "Irrigazione catetere", icon: Droplets },
  { id: "espianto_picc", label: "Espianto PICC", icon: CircleDot, isEspianto: true },
  { id: "espianto_picc_port", label: "Espianto PICC Port", icon: CircleDot, isEspianto: true },
  { id: "espianto_midline", label: "Espianto Midline", icon: CircleDot, isEspianto: true },
];

const PRESTAZIONI_MED = [
  { id: "medicazione_semplice", label: "Medicazione semplice", icon: Bandage },
  { id: "fasciatura_semplice", label: "Fasciatura semplice", icon: CircleDot },
  { id: "iniezione_terapeutica", label: "Iniezione terapeutica", icon: Syringe },
  { id: "catetere_vescicale", label: "Catetere vescicale", icon: Droplets },
];

const getNextWorkingDay = (date, holidayList = []) => {
  let d = new Date(date);
  const dateStr = format(d, "yyyy-MM-dd");
  // Skip weekends and holidays
  while (isWeekend(d) || holidayList.includes(format(d, "yyyy-MM-dd"))) {
    d = addDays(d, 1);
  }
  return d;
};

export default function AgendaPage() {
  const { ambulatorio } = useAmbulatorio();
  const navigate = useNavigate();
  const [currentDate, setCurrentDate] = useState(new Date());
  const [initialLoadDone, setInitialLoadDone] = useState(false);
  const [appointments, setAppointments] = useState([]);
  const [patients, setPatients] = useState([]);
  const [holidays, setHolidays] = useState([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [createPatientDialogOpen, setCreatePatientDialogOpen] = useState(false);
  const [selectedSlot, setSelectedSlot] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [filteredPatients, setFilteredPatients] = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [selectedPrestazioni, setSelectedPrestazioni] = useState([]);
  const [calendarOpen, setCalendarOpen] = useState(false);
  
  // Edit appointment dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false);
  const [editingAppointment, setEditingAppointment] = useState(null);
  const [editPrestazioni, setEditPrestazioni] = useState([]);
  
  // Closed slots state
  const [closedSlots, setClosedSlots] = useState([]);
  const [closeAgendaDialogOpen, setCloseAgendaDialogOpen] = useState(false);
  const [reopenDialogOpen, setReopenDialogOpen] = useState(false);
  const [closeMode, setCloseMode] = useState("slot"); // "slot" o "day"
  const [closeSlotOre, setCloseSlotOre] = useState([]); // Array di orari selezionati
  const [closeSlotTipo, setCloseSlotTipo] = useState("both"); // "PICC", "MED", "both"
  const [closeMotivo, setCloseMotivo] = useState("");
  
  // Timer per gestire click singolo vs doppio click
  const clickTimerRef = useRef(null);
  
  // New patient form state
  const [newPatientNome, setNewPatientNome] = useState("");
  const [newPatientCognome, setNewPatientCognome] = useState("");

  const isVillaGinestre = ambulatorio === "villa_ginestre";

  // Naviga alla cartella clinica del paziente
  const goToPatientFolder = (patientId) => {
    navigate(`/pazienti/${patientId}`);
  };

  // Gestisce click sul chip paziente con distinzione singolo/doppio click
  const handlePatientChipClick = (e, apt) => {
    e.stopPropagation();
    
    if (clickTimerRef.current) {
      // Doppio click: cancella timer e vai alla cartella
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
      goToPatientFolder(apt.patient_id);
    } else {
      // Primo click: imposta timer per aprire popup
      clickTimerRef.current = setTimeout(() => {
        clickTimerRef.current = null;
        handleOpenEditDialog(e, apt);
      }, 250); // 250ms per distinguere doppio click
    }
  };

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const dateStr = format(currentDate, "yyyy-MM-dd");
      const [appointmentsRes, patientsRes, holidaysRes, closedSlotsRes] = await Promise.all([
        apiClient.get("/appointments", {
          params: { ambulatorio, data: dateStr },
        }),
        apiClient.get("/patients", {
          params: { ambulatorio, status: "in_cura" },
        }),
        apiClient.get("/calendar/holidays", {
          params: { anno: currentDate.getFullYear() },
        }),
        apiClient.get("/closed-slots", {
          params: { ambulatorio, data: dateStr },
        }),
      ]);

      setAppointments(appointmentsRes.data);
      setPatients(patientsRes.data);
      setHolidays(holidaysRes.data);
      setClosedSlots(closedSlotsRes.data || []);
      
      // Set initial working day after holidays are loaded
      if (!initialLoadDone) {
        const workingDay = getNextWorkingDay(new Date(), holidaysRes.data);
        if (format(workingDay, "yyyy-MM-dd") !== format(currentDate, "yyyy-MM-dd")) {
          setCurrentDate(workingDay);
        }
        setInitialLoadDone(true);
      }
    } catch (error) {
      console.error("Error fetching agenda data:", error);
      // Only show error for network issues, not for empty data
      if (error.response?.status === 401) {
        // Token expired - will be handled by interceptor
      } else if (error.code === 'ERR_NETWORK') {
        toast.error("Errore di connessione al server");
      }
      // Silently handle other errors - data will just be empty
    } finally {
      setLoading(false);
    }
  }, [ambulatorio, currentDate, initialLoadDone]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  useEffect(() => {
    if (searchQuery.length >= 1 && selectedSlot) {
      const tipo = selectedSlot.tipo;
      const filtered = patients.filter((p) => {
        const matchesSearch =
          p.nome.toLowerCase().includes(searchQuery.toLowerCase()) ||
          p.cognome.toLowerCase().includes(searchQuery.toLowerCase());
        const matchesTipo =
          p.tipo === tipo || p.tipo === "PICC_MED";
        return matchesSearch && matchesTipo;
      });
      setFilteredPatients(filtered);
    } else {
      setFilteredPatients([]);
    }
  }, [searchQuery, patients, selectedSlot]);

  const goToToday = () => setCurrentDate(getNextWorkingDay(new Date(), holidays));
  const goToPrevDay = () => {
    let newDate = subDays(currentDate, 1);
    while (isWeekend(newDate) || holidays.includes(format(newDate, "yyyy-MM-dd"))) {
      newDate = subDays(newDate, 1);
    }
    setCurrentDate(new Date(newDate));
  };
  const goToNextDay = () => {
    let newDate = addDays(currentDate, 1);
    while (isWeekend(newDate) || holidays.includes(format(newDate, "yyyy-MM-dd"))) {
      newDate = addDays(newDate, 1);
    }
    setCurrentDate(new Date(newDate));
  };

  const isHoliday = (date) => {
    const dateStr = format(date, "yyyy-MM-dd");
    return isWeekend(date) || holidays.includes(dateStr);
  };

  const getAppointmentsForSlot = (ora, tipo) => {
    return appointments.filter((a) => a.ora === ora && a.tipo === tipo);
  };

  // Verifica se uno slot è chiuso
  const isSlotClosed = (ora, tipo) => {
    return closedSlots.some(cs => {
      // Giornata intera chiusa
      if (!cs.ora && !cs.tipo) return true;
      // Giornata intera per un tipo
      if (!cs.ora && cs.tipo === tipo) return true;
      // Slot specifico per entrambi i tipi
      if (cs.ora === ora && !cs.tipo) return true;
      // Slot specifico per un tipo
      if (cs.ora === ora && cs.tipo === tipo) return true;
      return false;
    });
  };

  // Ottiene info sullo slot chiuso
  const getClosedSlotInfo = (ora, tipo) => {
    return closedSlots.find(cs => {
      if (!cs.ora && !cs.tipo) return true;
      if (!cs.ora && cs.tipo === tipo) return true;
      if (cs.ora === ora && !cs.tipo) return true;
      if (cs.ora === ora && cs.tipo === tipo) return true;
      return false;
    });
  };

  // Verifica se tutta la giornata è chiusa
  const isDayClosed = () => {
    return closedSlots.some(cs => !cs.ora && !cs.tipo);
  };

  // Chiudi slot o giornata
  const handleCloseAgenda = async () => {
    try {
      const dateStr = format(currentDate, "yyyy-MM-dd");
      const payload = {
        data: dateStr,
        ambulatorio,
        motivo: closeMotivo || "Chiuso"
      };

      if (closeMode === "day") {
        // Chiudi tutta la giornata
        payload.ora = null;
        payload.tipo = null;
      } else {
        // Chiudi slot specifici (può essere multiplo)
        if (closeSlotOre.length === 0) {
          toast.error("Seleziona almeno un orario");
          return;
        }
        payload.ora = closeSlotOre.length === 1 ? closeSlotOre[0] : closeSlotOre;
        payload.tipo = closeSlotTipo === "both" ? null : closeSlotTipo;
      }

      const response = await apiClient.post("/closed-slots", payload);
      const count = response.data.created || 1;
      toast.success(closeMode === "day" ? "Giornata chiusa" : `${count} slot chiusi`);
      setCloseAgendaDialogOpen(false);
      resetCloseForm();
      fetchData();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nella chiusura");
    }
  };

  // Riapri uno slot
  const handleReopenSlot = async (slotId) => {
    try {
      await apiClient.delete(`/closed-slots/${slotId}`);
      toast.success("Slot riaperto");
      fetchData();
    } catch (error) {
      toast.error("Errore nella riapertura");
    }
  };

  // Riapri tutta la giornata
  const handleReopenDay = async () => {
    try {
      const dateStr = format(currentDate, "yyyy-MM-dd");
      await apiClient.post("/closed-slots/reopen-day", {
        ambulatorio,
        data: dateStr
      });
      toast.success("Giornata riaperta");
      setReopenDialogOpen(false);
      fetchData();
    } catch (error) {
      toast.error("Errore nella riapertura");
    }
  };

  // Toggle selezione orario
  const toggleSlotOra = (ora) => {
    setCloseSlotOre(prev => 
      prev.includes(ora) 
        ? prev.filter(o => o !== ora)
        : [...prev, ora]
    );
  };

  const resetCloseForm = () => {
    setCloseMode("slot");
    setCloseSlotOre([]);
    setCloseSlotTipo("both");
    setCloseMotivo("");
  };

  const handleSlotClick = (ora, tipo) => {
    if (isHoliday(currentDate)) return;
    
    // Verifica se lo slot è chiuso
    if (isSlotClosed(ora, tipo)) {
      // Apri il dialog per gestire le chiusure
      setReopenDialogOpen(true);
      return;
    }
    
    const existing = getAppointmentsForSlot(ora, tipo);
    if (existing.length >= 2) {
      toast.error("Slot pieno (max 2 pazienti)");
      return;
    }
    setSelectedSlot({ ora, tipo });
    setSearchQuery("");
    setSelectedPatient(null);
    setSelectedPrestazioni([]);
    setDialogOpen(true);
  };

  const handlePatientSelect = (patient) => {
    setSelectedPatient(patient);
    setSearchQuery(`${patient.cognome} ${patient.nome}`);
    setFilteredPatients([]);
    
    // Auto-seleziona prestazioni per pazienti PICC
    // I pazienti PICC (o PICC_MED quando si prenota in slot PICC) avranno 
    // automaticamente medicazione e irrigazione pre-selezionate
    if (selectedSlot?.tipo === "PICC" && (patient.tipo === "PICC" || patient.tipo === "PICC_MED")) {
      setSelectedPrestazioni(["medicazione_semplice", "irrigazione_catetere"]);
    }
  };

  const handlePrestazioneToggle = (prestazioneId) => {
    setSelectedPrestazioni((prev) =>
      prev.includes(prestazioneId)
        ? prev.filter((p) => p !== prestazioneId)
        : [...prev, prestazioneId]
    );
  };

  const handleAddAppointment = async () => {
    if (!selectedPatient) {
      toast.error("Seleziona un paziente");
      return;
    }
    if (selectedPrestazioni.length === 0) {
      toast.error("Seleziona almeno una prestazione");
      return;
    }

    try {
      await apiClient.post("/appointments", {
        patient_id: selectedPatient.id,
        ambulatorio,
        data: format(currentDate, "yyyy-MM-dd"),
        ora: selectedSlot.ora,
        tipo: selectedSlot.tipo,
        prestazioni: selectedPrestazioni,
      });

      toast.success("Appuntamento aggiunto");
      setDialogOpen(false);
      fetchData();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nell'aggiunta");
    }
  };

  const handleDeleteAppointment = async (appointmentId) => {
    try {
      await apiClient.delete(`/appointments/${appointmentId}`);
      toast.success("Appuntamento rimosso");
      setEditDialogOpen(false);
      setEditingAppointment(null);
      fetchData();
    } catch (error) {
      toast.error("Errore nella rimozione");
    }
  };

  // Apre dialog di modifica appuntamento
  const handleOpenEditDialog = (e, appointment) => {
    e.stopPropagation();
    setEditingAppointment(appointment);
    setEditPrestazioni(appointment.prestazioni || []);
    setEditDialogOpen(true);
  };

  // Cambia stato appuntamento
  const handleChangeStato = async (newStato) => {
    if (!editingAppointment) return;
    try {
      await apiClient.put(`/appointments/${editingAppointment.id}`, { stato: newStato });
      toast.success(newStato === "effettuato" ? "Segnato come effettuato" : "Segnato come non presentato");
      setEditDialogOpen(false);
      setEditingAppointment(null);
      fetchData();
    } catch (error) {
      toast.error("Errore nel cambio stato");
    }
  };

  // Salva modifiche prestazioni
  const handleSavePrestazioni = async () => {
    if (!editingAppointment || editPrestazioni.length === 0) {
      toast.error("Seleziona almeno una prestazione");
      return;
    }
    try {
      await apiClient.put(`/appointments/${editingAppointment.id}`, { prestazioni: editPrestazioni });
      toast.success("Prestazioni aggiornate");
      setEditDialogOpen(false);
      setEditingAppointment(null);
      fetchData();
    } catch (error) {
      toast.error("Errore nell'aggiornamento");
    }
  };

  const handleEditPrestazioneToggle = (prestazioneId) => {
    setEditPrestazioni((prev) =>
      prev.includes(prestazioneId)
        ? prev.filter((p) => p !== prestazioneId)
        : [...prev, prestazioneId]
    );
  };

  const handleCreatePatient = async () => {
    if (!newPatientNome || !newPatientCognome) {
      toast.error("Inserisci nome e cognome");
      return;
    }

    try {
      const response = await apiClient.post("/patients", {
        nome: newPatientNome,
        cognome: newPatientCognome,
        tipo: selectedSlot?.tipo || "PICC",
        ambulatorio,
      });

      toast.success("Paziente creato");
      setCreatePatientDialogOpen(false);
      setNewPatientNome("");
      setNewPatientCognome("");
      
      // Refresh patients and select the new one
      await fetchData();
      setSelectedPatient(response.data);
      setSearchQuery(`${response.data.cognome} ${response.data.nome}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nella creazione");
    }
  };

  const prestazioni = selectedSlot?.tipo === "PICC" ? PRESTAZIONI_PICC : PRESTAZIONI_MED;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    );
  }

  const holidayToday = isHoliday(currentDate);

  return (
    <div className="animate-fade-in" data-testid="agenda-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Agenda</h1>
          <p className="text-muted-foreground text-sm">
            Gestione appuntamenti giornalieri
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <Button
            variant="outline"
            size="icon"
            onClick={goToPrevDay}
            data-testid="agenda-prev-day"
          >
            <ChevronLeft className="w-4 h-4" />
          </Button>

          <Popover open={calendarOpen} onOpenChange={setCalendarOpen}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                className="min-w-[220px] justify-start font-medium"
                data-testid="agenda-date-picker"
              >
                <CalendarIcon className="mr-2 h-4 w-4" />
                {format(currentDate, "EEEE d MMMM yyyy", { locale: it })}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-auto p-0" align="center">
              <Calendar
                mode="single"
                selected={currentDate}
                onSelect={(date) => {
                  if (date) {
                    setCurrentDate(date);
                    setCalendarOpen(false);
                  }
                }}
                locale={it}
                disabled={(date) => isWeekend(date)}
              />
            </PopoverContent>
          </Popover>

          <Button
            variant="outline"
            size="icon"
            onClick={goToNextDay}
            data-testid="agenda-next-day"
          >
            <ChevronRight className="w-4 h-4" />
          </Button>

          <Button variant="secondary" size="sm" onClick={goToToday}>
            Oggi
          </Button>

          {/* Pulsante Chiudi Agenda */}
          <Button 
            variant="outline" 
            size="sm" 
            onClick={() => setCloseAgendaDialogOpen(true)}
            className="ml-4 text-red-600 border-red-300 hover:bg-red-50"
          >
            <Lock className="w-4 h-4 mr-2" />
            Chiudi Agenda
          </Button>

          {/* Pulsante Gestisci Chiusure (solo se ci sono slot chiusi) */}
          {closedSlots.length > 0 && (
            <Button 
              variant="outline" 
              size="sm" 
              onClick={() => setReopenDialogOpen(true)}
              className="text-green-600 border-green-300 hover:bg-green-50"
            >
              <Unlock className="w-4 h-4 mr-2" />
              Gestisci Chiusure ({closedSlots.length})
            </Button>
          )}
        </div>
      </div>

      {/* Holiday notice */}
      {holidayToday && (
        <div className="mb-4 p-4 bg-slate-100 border border-slate-200 rounded-lg">
          <p className="text-sm text-slate-600 font-medium">
            Giorno non lavorativo - Prenotazioni non disponibili
          </p>
        </div>
      )}

      {/* Agenda Grid */}
      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <div 
              className="grid gap-px bg-border"
              style={{ 
                gridTemplateColumns: isVillaGinestre 
                  ? "80px 1fr" 
                  : "80px 1fr 1fr",
                minWidth: isVillaGinestre ? "400px" : "600px"
              }}
            >
              {/* Headers */}
              <div className="bg-primary text-primary-foreground font-semibold p-3 text-center text-sm">
                Ora
              </div>
              <div className="bg-emerald-600 text-white font-semibold p-3 text-center text-sm">
                PICC
              </div>
              {!isVillaGinestre && (
                <div className="bg-primary text-primary-foreground font-semibold p-3 text-center text-sm">
                  MED
                </div>
              )}

              {/* Time slots */}
              {TIME_SLOTS.map((ora) => (
                <>
                  <div key={`time-${ora}`} className="bg-muted font-medium text-sm p-2 flex items-center justify-center">
                    {ora}
                  </div>

                  {/* PICC Column */}
                  <div
                    key={`picc-${ora}`}
                    className={`bg-card min-h-[70px] p-2 ${
                      holidayToday 
                        ? "bg-muted cursor-not-allowed" 
                        : isSlotClosed(ora, "PICC")
                          ? "bg-red-50 cursor-pointer border-l-4 border-red-400"
                          : "cursor-pointer hover:bg-emerald-50"
                    }`}
                    onClick={() => !holidayToday && handleSlotClick(ora, "PICC")}
                    data-testid={`agenda-slot-${ora}-picc`}
                  >
                    {isSlotClosed(ora, "PICC") ? (
                      <div className="flex items-center justify-center h-full">
                        <div className="flex items-center gap-2 text-red-500">
                          <Ban className="w-4 h-4" />
                          <span className="text-sm font-medium">Chiuso</span>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="flex flex-wrap gap-2">
                          {getAppointmentsForSlot(ora, "PICC").map((apt) => (
                            <div
                              key={apt.id}
                              className={`relative px-4 py-2 rounded-lg border-2 cursor-pointer transition-all shadow-sm hover:shadow-md ${getStatoColorClass(apt.stato || "da_fare")}`}
                              title={`Click: gestisci | Doppio click: vai alla cartella`}
                              onClick={(e) => handlePatientChipClick(e, apt)}
                            >
                              <span className="font-bold text-base block">{apt.patient_cognome} {apt.patient_nome?.charAt(0)}.</span>
                            </div>
                          ))}
                        </div>
                        {!holidayToday && getAppointmentsForSlot(ora, "PICC").length < 2 && (
                          <div className="text-xs text-muted-foreground opacity-0 hover:opacity-100 transition-opacity flex items-center justify-center mt-1">
                            <Plus className="w-3 h-3 mr-1" /> Aggiungi
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {/* MED Column (only for PTA Centro) */}
                  {!isVillaGinestre && (
                    <div
                      key={`med-${ora}`}
                      className={`bg-card min-h-[70px] p-2 ${
                        holidayToday 
                          ? "bg-muted cursor-not-allowed" 
                          : isSlotClosed(ora, "MED")
                            ? "bg-red-50 cursor-pointer border-l-4 border-red-400"
                            : "cursor-pointer hover:bg-blue-50"
                      }`}
                      onClick={() => !holidayToday && handleSlotClick(ora, "MED")}
                      data-testid={`agenda-slot-${ora}-med`}
                    >
                      {isSlotClosed(ora, "MED") ? (
                        <div className="flex items-center justify-center h-full">
                          <div className="flex items-center gap-2 text-red-500">
                            <Ban className="w-4 h-4" />
                            <span className="text-sm font-medium">Chiuso</span>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="flex flex-wrap gap-2">
                            {getAppointmentsForSlot(ora, "MED").map((apt) => (
                              <div
                                key={apt.id}
                                className={`relative px-4 py-2 rounded-lg border-2 cursor-pointer transition-all shadow-sm hover:shadow-md ${getStatoColorClass(apt.stato || "da_fare")}`}
                                title={`Click: gestisci | Doppio click: vai alla cartella`}
                                onClick={(e) => handlePatientChipClick(e, apt)}
                              >
                                <span className="font-bold text-base block">{apt.patient_cognome} {apt.patient_nome?.charAt(0)}.</span>
                              </div>
                            ))}
                          </div>
                          {!holidayToday && getAppointmentsForSlot(ora, "MED").length < 2 && (
                            <div className="text-xs text-muted-foreground opacity-0 hover:opacity-100 transition-opacity flex items-center justify-center mt-1">
                              <Plus className="w-3 h-3 mr-1" /> Aggiungi
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Add Appointment Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nuovo Appuntamento</DialogTitle>
            <DialogDescription>
              {selectedSlot && (
                <>
                  {format(currentDate, "d MMMM yyyy", { locale: it })} alle {selectedSlot.ora} - {selectedSlot.tipo}
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Patient Search */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>Paziente</Label>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCreatePatientDialogOpen(true)}
                  className="h-7 text-xs"
                >
                  <UserPlus className="w-3 h-3 mr-1" />
                  Nuovo paziente
                </Button>
              </div>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input
                  data-testid="agenda-patient-search"
                  placeholder="Cerca per nome o cognome..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9"
                />
              </div>

              {filteredPatients.length > 0 && (
                <ScrollArea className="h-40 border rounded-md">
                  <div className="p-2">
                    {filteredPatients.map((patient) => (
                      <div
                        key={patient.id}
                        data-testid={`agenda-patient-option-${patient.id}`}
                        className="p-2 hover:bg-accent rounded cursor-pointer flex items-center justify-between"
                        onClick={() => handlePatientSelect(patient)}
                      >
                        <span className="font-medium">
                          {patient.cognome} {patient.nome}
                        </span>
                        <span className={`text-xs px-2 py-0.5 rounded ${
                          patient.tipo === "PICC" ? "bg-emerald-100 text-emerald-700" :
                          patient.tipo === "MED" ? "bg-blue-100 text-blue-700" :
                          "bg-purple-100 text-purple-700"
                        }`}>
                          {patient.tipo}
                        </span>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              )}

              {selectedPatient && (
                <div className="p-2 bg-accent rounded-md flex items-center justify-between">
                  <span>
                    <strong>{selectedPatient.cognome} {selectedPatient.nome}</strong>
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setSelectedPatient(null);
                      setSearchQuery("");
                    }}
                  >
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              )}
            </div>

            {/* Prestazioni */}
            <div className="space-y-2">
              <Label>Prestazioni (seleziona una o più)</Label>
              <div className="grid gap-2">
                {prestazioni.map((prest) => (
                  <div
                    key={prest.id}
                    className={`flex items-center gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                      selectedPrestazioni.includes(prest.id)
                        ? "border-primary bg-primary/5"
                        : "hover:border-primary/50"
                    }`}
                    onClick={() => handlePrestazioneToggle(prest.id)}
                    data-testid={`agenda-prestazione-${prest.id}`}
                  >
                    <Checkbox
                      checked={selectedPrestazioni.includes(prest.id)}
                      onCheckedChange={() => handlePrestazioneToggle(prest.id)}
                    />
                    <prest.icon className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm">{prest.label}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                Annulla
              </Button>
              <Button
                onClick={handleAddAppointment}
                disabled={!selectedPatient || selectedPrestazioni.length === 0}
                data-testid="agenda-add-appointment-btn"
              >
                Aggiungi
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Create Patient Dialog */}
      <Dialog open={createPatientDialogOpen} onOpenChange={setCreatePatientDialogOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Nuovo Paziente Rapido</DialogTitle>
            <DialogDescription>
              Crea un nuovo paziente {selectedSlot?.tipo || "PICC"} per aggiungerlo subito in agenda
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Cognome *</Label>
              <Input
                value={newPatientCognome}
                onChange={(e) => setNewPatientCognome(e.target.value)}
                placeholder="Cognome"
                data-testid="quick-patient-cognome"
              />
            </div>
            <div className="space-y-2">
              <Label>Nome *</Label>
              <Input
                value={newPatientNome}
                onChange={(e) => setNewPatientNome(e.target.value)}
                placeholder="Nome"
                data-testid="quick-patient-nome"
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setCreatePatientDialogOpen(false)}>
                Annulla
              </Button>
              <Button onClick={handleCreatePatient} data-testid="quick-patient-create-btn">
                Crea e Seleziona
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Edit Appointment Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Gestisci Appuntamento</DialogTitle>
            <DialogDescription>
              {editingAppointment && (
                <>
                  {editingAppointment.patient_cognome} {editingAppointment.patient_nome} - {editingAppointment.ora} ({editingAppointment.tipo})
                </>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Stato */}
            <div className="space-y-2">
              <Label>Stato Appuntamento</Label>
              <div className="flex gap-2">
                <Button
                  variant={editingAppointment?.stato === "effettuato" ? "default" : "outline"}
                  size="sm"
                  onClick={() => handleChangeStato("effettuato")}
                  className={`flex-1 ${editingAppointment?.stato === "effettuato" ? "bg-green-600 hover:bg-green-700" : "hover:bg-green-50 hover:text-green-700 hover:border-green-300"}`}
                >
                  ✓ Effettuato
                </Button>
                <Button
                  variant={editingAppointment?.stato === "non_presentato" ? "destructive" : "outline"}
                  size="sm"
                  onClick={() => handleChangeStato("non_presentato")}
                  className={`flex-1 ${editingAppointment?.stato !== "non_presentato" ? "hover:bg-red-50 hover:text-red-700 hover:border-red-300" : ""}`}
                >
                  ✗ Non Presentato
                </Button>
              </div>
            </div>

            {/* Prestazioni */}
            <div className="space-y-2">
              <Label>Prestazioni</Label>
              <div className="grid gap-2">
                {(editingAppointment?.tipo === "PICC" ? PRESTAZIONI_PICC : PRESTAZIONI_MED).map((prest) => (
                  <div
                    key={prest.id}
                    className={`flex items-center gap-3 p-3 border rounded-lg cursor-pointer transition-colors ${
                      editPrestazioni.includes(prest.id)
                        ? "border-primary bg-primary/5"
                        : "hover:border-primary/50"
                    }`}
                    onClick={() => handleEditPrestazioneToggle(prest.id)}
                  >
                    <Checkbox
                      checked={editPrestazioni.includes(prest.id)}
                      onCheckedChange={() => handleEditPrestazioneToggle(prest.id)}
                    />
                    <prest.icon className="w-4 h-4 text-muted-foreground" />
                    <span className="text-sm">{prest.label}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex justify-between pt-4">
              <div className="flex gap-2">
                <Button
                  variant="destructive"
                  onClick={() => handleDeleteAppointment(editingAppointment?.id)}
                >
                  Elimina
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setEditDialogOpen(false);
                    goToPatientFolder(editingAppointment?.patient_id);
                  }}
                  className="border-blue-300 text-blue-600 hover:bg-blue-50"
                >
                  <ExternalLink className="w-4 h-4 mr-1" />
                  Apri Cartella
                </Button>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => setEditDialogOpen(false)}>
                  Annulla
                </Button>
                <Button onClick={handleSavePrestazioni}>
                  Salva Prestazioni
                </Button>
              </div>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Dialog Chiudi Agenda */}
      <Dialog open={closeAgendaDialogOpen} onOpenChange={setCloseAgendaDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Lock className="w-5 h-5 text-red-500" />
              Chiudi Agenda
            </DialogTitle>
            <DialogDescription>
              Chiudi uno o più slot o l'intera giornata del {format(currentDate, "d MMMM yyyy", { locale: it })}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            {/* Selezione modalità */}
            <div className="space-y-2">
              <Label>Cosa vuoi chiudere?</Label>
              <div className="flex gap-2">
                <Button
                  variant={closeMode === "slot" ? "default" : "outline"}
                  onClick={() => setCloseMode("slot")}
                  className="flex-1"
                >
                  Slot Specifici
                </Button>
                <Button
                  variant={closeMode === "day" ? "default" : "outline"}
                  onClick={() => setCloseMode("day")}
                  className="flex-1"
                >
                  Tutta la Giornata
                </Button>
              </div>
            </div>

            {/* Opzioni per slot specifici */}
            {closeMode === "slot" && (
              <>
                <div className="space-y-2">
                  <Label>Seleziona orari (click per selezionare/deselezionare)</Label>
                  <div className="grid grid-cols-4 gap-2 max-h-48 overflow-y-auto p-2 border rounded-lg">
                    {TIME_SLOTS.map((ora) => {
                      const isSelected = closeSlotOre.includes(ora);
                      const isClosed = isSlotClosed(ora, closeSlotTipo === "both" ? "PICC" : closeSlotTipo);
                      return (
                        <Button
                          key={ora}
                          variant={isSelected ? "default" : "outline"}
                          size="sm"
                          onClick={() => toggleSlotOra(ora)}
                          disabled={isClosed}
                          className={`${isSelected ? "bg-red-500 hover:bg-red-600" : ""} ${isClosed ? "opacity-50" : ""}`}
                        >
                          {ora}
                        </Button>
                      );
                    })}
                  </div>
                  {closeSlotOre.length > 0 && (
                    <p className="text-sm text-muted-foreground">
                      {closeSlotOre.length} orari selezionati: {closeSlotOre.join(", ")}
                    </p>
                  )}
                </div>

                <div className="space-y-2">
                  <Label>Tipo</Label>
                  <Select value={closeSlotTipo} onValueChange={setCloseSlotTipo}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="both">Entrambi (PICC e MED)</SelectItem>
                      <SelectItem value="PICC">Solo PICC</SelectItem>
                      {!isVillaGinestre && <SelectItem value="MED">Solo MED</SelectItem>}
                    </SelectContent>
                  </Select>
                </div>
              </>
            )}

            {/* Motivo */}
            <div className="space-y-2">
              <Label>Motivo (opzionale)</Label>
              <Input
                placeholder="Es: Ferie, Formazione, Manutenzione..."
                value={closeMotivo}
                onChange={(e) => setCloseMotivo(e.target.value)}
              />
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => {
              setCloseAgendaDialogOpen(false);
              resetCloseForm();
            }}>
              Annulla
            </Button>
            <Button 
              variant="destructive" 
              onClick={handleCloseAgenda}
              disabled={closeMode === "slot" && closeSlotOre.length === 0}
            >
              <Lock className="w-4 h-4 mr-2" />
              {closeMode === "day" ? "Chiudi Giornata" : `Chiudi ${closeSlotOre.length} Slot`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Dialog Gestisci Chiusure */}
      <Dialog open={reopenDialogOpen} onOpenChange={setReopenDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Unlock className="w-5 h-5 text-green-500" />
              Gestisci Chiusure
            </DialogTitle>
            <DialogDescription>
              Slot chiusi per il {format(currentDate, "d MMMM yyyy", { locale: it })}. Clicca su uno slot per riaprirlo.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            {closedSlots.length === 0 ? (
              <p className="text-center text-muted-foreground py-4">Nessuno slot chiuso</p>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {closedSlots.map((slot) => (
                  <div 
                    key={slot.id} 
                    className="flex items-center justify-between p-3 bg-red-50 border border-red-200 rounded-lg"
                  >
                    <div className="flex items-center gap-3">
                      <Ban className="w-4 h-4 text-red-500" />
                      <div>
                        <p className="font-medium">
                          {slot.ora ? `Ore ${slot.ora}` : "Tutta la giornata"}
                          {slot.tipo && ` - Solo ${slot.tipo}`}
                        </p>
                        {slot.motivo && (
                          <p className="text-sm text-muted-foreground">{slot.motivo}</p>
                        )}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleReopenSlot(slot.id)}
                      className="text-green-600 border-green-300 hover:bg-green-50"
                    >
                      <Unlock className="w-4 h-4 mr-1" />
                      Riapri
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex justify-between">
            <Button 
              variant="outline" 
              onClick={handleReopenDay}
              className="text-green-600"
              disabled={closedSlots.length === 0}
            >
              <Unlock className="w-4 h-4 mr-2" />
              Riapri Tutto
            </Button>
            <Button variant="outline" onClick={() => setReopenDialogOpen(false)}>
              Chiudi
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
