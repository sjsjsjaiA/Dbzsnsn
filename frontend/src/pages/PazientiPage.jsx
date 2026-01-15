import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAmbulatorio, apiClient } from "@/App";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Search,
  Plus,
  Users,
  UserCheck,
  UserX,
  ChevronRight,
  MoreVertical,
  Pause,
  Play,
  Trash2,
  Filter,
  ChevronDown,
  CheckSquare,
  Square,
  ListChecks,
  X,
  Pencil,
  RotateCcw,
  Calendar,
  FileText,
  Hash,
} from "lucide-react";
import { toast } from "sonner";

// Prestazioni MED disponibili
const PRESTAZIONI_MED = [
  { value: "medicazione_semplice", label: "Medicazione semplice" },
  { value: "fasciatura_semplice", label: "Fasciatura semplice" },
  { value: "iniezione_terapeutica", label: "Iniezione terapeutica" },
  { value: "catetere_vescicale", label: "Catetere vescicale" },
];

const PATIENT_TYPES = [
  { value: "PICC", label: "PICC", color: "bg-emerald-100 text-emerald-700" },
  { value: "MED", label: "MED", color: "bg-blue-100 text-blue-700" },
  { value: "PICC_MED", label: "PICC + MED", color: "bg-purple-100 text-purple-700" },
];

// Simple custom select component without portal issues
const SimpleSelect = ({ value, onChange, options, placeholder, className = "" }) => {
  const [isOpen, setIsOpen] = useState(false);
  const selectRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (selectRef.current && !selectRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedOption = options.find(opt => opt.value === value);

  return (
    <div ref={selectRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        <span className={selectedOption ? "" : "text-muted-foreground"}>
          {selectedOption?.label || placeholder}
        </span>
        <ChevronDown className={`h-4 w-4 opacity-50 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      {isOpen && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-popover text-popover-foreground shadow-md max-h-60 overflow-auto">
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`w-full px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground ${
                value === option.value ? 'bg-accent/50' : ''
              }`}
              onClick={() => {
                onChange(option.value);
                setIsOpen(false);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

// Simple dropdown menu without portal issues
const SimpleDropdown = ({ trigger, children, align = "end" }) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={dropdownRef} className="relative">
      <div onClick={(e) => { e.stopPropagation(); setIsOpen(!isOpen); }}>
        {trigger}
      </div>
      {isOpen && (
        <div 
          className={`absolute z-50 mt-1 min-w-[180px] rounded-md border bg-popover p-1 text-popover-foreground shadow-md ${
            align === "end" ? "right-0" : "left-0"
          }`}
          onClick={() => setIsOpen(false)}
        >
          {children}
        </div>
      )}
    </div>
  );
};

const DropdownItem = ({ onClick, icon: Icon, children, className = "" }) => (
  <button
    type="button"
    onClick={onClick}
    className={`flex w-full items-center rounded-sm px-2 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground ${className}`}
  >
    {Icon && <Icon className="w-4 h-4 mr-2" />}
    {children}
  </button>
);

const DropdownSeparator = () => <div className="my-1 h-px bg-muted" />;

export default function PazientiPage() {
  const { ambulatorio } = useAmbulatorio();
  const navigate = useNavigate();
  const [patients, setPatients] = useState([]);
  const [allPatients, setAllPatients] = useState({ in_cura: [], dimesso: [], sospeso: [] });
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeTab, setActiveTab] = useState("in_cura");
  const [typeFilter, setTypeFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [batchDialogOpen, setBatchDialogOpen] = useState(false);
  const [statusDialogOpen, setStatusDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [batchStatusDialogOpen, setBatchStatusDialogOpen] = useState(false);
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false);
  const [selectedPatientForStatus, setSelectedPatientForStatus] = useState(null);
  const [selectedPatientForDelete, setSelectedPatientForDelete] = useState(null);
  const [newStatus, setNewStatus] = useState("");
  const [statusReason, setStatusReason] = useState("");
  const [statusNotes, setStatusNotes] = useState("");
  const [newPatient, setNewPatient] = useState({
    nome: "",
    cognome: "",
    tipo: "",
  });
  
  // Batch selection state
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedPatients, setSelectedPatients] = useState(new Set());
  const [batchAction, setBatchAction] = useState(null); // 'suspend', 'resume', 'discharge', 'delete'
  
  // Batch create state
  const [batchPatients, setBatchPatients] = useState([{ nome: "", cognome: "", tipo: "", tipo_impianto: "", data_inserimento_impianto: "" }]);
  const [batchTab, setBatchTab] = useState("pazienti"); // "pazienti" o "impianti"
  
  // Batch implants state
  const [batchImplants, setBatchImplants] = useState([{ patient_id: "", patient_name: "", tipo_impianto: "", data_inserimento: "" }]);
  const [piccPatientSearch, setPiccPatientSearch] = useState("");
  const [piccPatientResults, setPiccPatientResults] = useState([]);
  const [searchingPicc, setSearchingPicc] = useState(false);
  const [activeSearchIndex, setActiveSearchIndex] = useState(null);
  
  // Ricetta MED state
  const [editingMedPatient, setEditingMedPatient] = useState(null);
  const [medRicetta, setMedRicetta] = useState([]);
  const [medQuantita, setMedQuantita] = useState("");
  const [medDataInizio, setMedDataInizio] = useState("");
  const [medDialogOpen, setMedDialogOpen] = useState(false);

  const isVillaGinestre = ambulatorio === "villa_ginestre";
  const availableTypes = isVillaGinestre 
    ? PATIENT_TYPES.filter(t => t.value === "PICC")
    : PATIENT_TYPES;

  // Options for selects
  const typeFilterOptions = [
    { value: "all", label: "Tutti i tipi" },
    { value: "PICC", label: "Solo PICC" },
    { value: "MED", label: "Solo MED" },
    { value: "PICC_MED", label: "Solo PICC+MED" },
  ];

  const patientTypeOptions = availableTypes.map(t => ({ value: t.value, label: t.label }));

  const dischargeReasonOptions = [
    { value: "guarito", label: "Guarito" },
    { value: "adi", label: "ADI" },
    { value: "altro", label: "Altro" },
  ];

  // Opzioni per tipo impianto PICC
  const tipoImpiantoOptions = [
    { value: "", label: "Seleziona tipo impianto" },
    { value: "picc", label: "PICC" },
    { value: "picc_port", label: "PICC Port" },
    { value: "midline", label: "Midline" },
  ];

  const fetchAllPatients = useCallback(async () => {
    setLoading(true);
    try {
      const [inCuraRes, dimessiRes, sospesiRes] = await Promise.all([
        apiClient.get("/patients", { params: { ambulatorio, status: "in_cura" } }),
        apiClient.get("/patients", { params: { ambulatorio, status: "dimesso" } }),
        apiClient.get("/patients", { params: { ambulatorio, status: "sospeso" } }),
      ]);
      
      setAllPatients({
        in_cura: inCuraRes.data,
        dimesso: dimessiRes.data,
        sospeso: sospesiRes.data,
      });
    } catch (error) {
      console.error("Error fetching patients:", error);
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
  }, [ambulatorio]);

  useEffect(() => {
    fetchAllPatients();
  }, [fetchAllPatients]);

  // Clear selection when changing tab
  useEffect(() => {
    setSelectedPatients(new Set());
  }, [activeTab]);

  // Filter patients based on active tab, search, and type filter
  const filteredPatients = allPatients[activeTab]?.filter(p => {
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      if (!p.nome?.toLowerCase().includes(query) && !p.cognome?.toLowerCase().includes(query)) {
        return false;
      }
    }
    if (typeFilter !== "all") {
      if (typeFilter === "PICC" && p.tipo !== "PICC" && p.tipo !== "PICC_MED") return false;
      if (typeFilter === "MED" && p.tipo !== "MED" && p.tipo !== "PICC_MED") return false;
      if (typeFilter === "PICC_MED" && p.tipo !== "PICC_MED") return false;
    }
    return true;
  }) || [];

  const getCounts = () => ({
    in_cura: allPatients.in_cura?.length || 0,
    dimesso: allPatients.dimesso?.length || 0,
    sospeso: allPatients.sospeso?.length || 0,
    picc_in_cura: allPatients.in_cura?.filter(p => p.tipo === "PICC" || p.tipo === "PICC_MED").length || 0,
    med_in_cura: allPatients.in_cura?.filter(p => p.tipo === "MED" || p.tipo === "PICC_MED").length || 0,
  });

  const counts = getCounts();

  const handleCreatePatient = async () => {
    if (!newPatient.nome || !newPatient.cognome || !newPatient.tipo) {
      toast.error("Compila tutti i campi obbligatori");
      return;
    }

    try {
      const response = await apiClient.post("/patients", {
        ...newPatient,
        ambulatorio,
      });
      toast.success("Paziente creato con successo");
      setDialogOpen(false);
      setNewPatient({ nome: "", cognome: "", tipo: "" });
      fetchAllPatients();
      navigate(`/pazienti/${response.data.id}`);
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nella creazione");
    }
  };

  // Batch create patients
  const handleBatchCreate = async () => {
    const validPatients = batchPatients.filter(p => p.nome && p.cognome && p.tipo);
    if (validPatients.length === 0) {
      toast.error("Inserisci almeno un paziente completo");
      return;
    }

    try {
      const response = await apiClient.post("/patients/batch", {
        patients: validPatients.map(p => ({
          nome: p.nome,
          cognome: p.cognome,
          tipo: p.tipo,
          ambulatorio,
          // Includi i dati dell'impianto solo se presenti
          ...(p.tipo_impianto && { tipo_impianto: p.tipo_impianto }),
          ...(p.data_inserimento_impianto && { data_inserimento_impianto: p.data_inserimento_impianto }),
        }))
      });
      
      if (response.data.created > 0) {
        let msg = `${response.data.created} pazienti creati con successo`;
        if (response.data.impianti_created > 0) {
          msg += `, ${response.data.impianti_created} schede impianto create`;
        }
        toast.success(msg);
      }
      if (response.data.errors > 0) {
        toast.warning(`${response.data.errors} pazienti non creati`);
      }
      
      setBatchDialogOpen(false);
      setBatchPatients([{ nome: "", cognome: "", tipo: "", tipo_impianto: "", data_inserimento_impianto: "" }]);
      fetchAllPatients();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nella creazione");
    }
  };

  const addBatchPatientRow = () => {
    setBatchPatients([...batchPatients, { nome: "", cognome: "", tipo: "", tipo_impianto: "", data_inserimento_impianto: "" }]);
  };

  const removeBatchPatientRow = (index) => {
    if (batchPatients.length > 1) {
      setBatchPatients(batchPatients.filter((_, i) => i !== index));
    }
  };

  const updateBatchPatient = (index, field, value) => {
    const updated = [...batchPatients];
    updated[index][field] = value;
    setBatchPatients(updated);
  };

  // ===== Batch Implants Functions =====
  const searchPiccPatients = async (query, index) => {
    if (!query || query.length < 2) {
      setPiccPatientResults([]);
      return;
    }
    
    setSearchingPicc(true);
    setActiveSearchIndex(index);
    try {
      const response = await apiClient.get(`/patients/picc/search?q=${encodeURIComponent(query)}&ambulatorio=${ambulatorio}`);
      setPiccPatientResults(response.data || []);
    } catch (error) {
      console.error("Errore ricerca pazienti PICC:", error);
      setPiccPatientResults([]);
    } finally {
      setSearchingPicc(false);
    }
  };

  const selectPiccPatient = (patient, index) => {
    const updated = [...batchImplants];
    updated[index].patient_id = patient.id;
    updated[index].patient_name = `${patient.cognome} ${patient.nome}`;
    setBatchImplants(updated);
    setPiccPatientResults([]);
    setActiveSearchIndex(null);
  };

  const updateBatchImplant = (index, field, value) => {
    const updated = [...batchImplants];
    updated[index][field] = value;
    setBatchImplants(updated);
  };

  const addBatchImplantRow = () => {
    setBatchImplants([...batchImplants, { patient_id: "", patient_name: "", tipo_impianto: "", data_inserimento: "" }]);
  };

  const removeBatchImplantRow = (index) => {
    if (batchImplants.length > 1) {
      setBatchImplants(batchImplants.filter((_, i) => i !== index));
    }
  };

  const handleBatchImplantsCreate = async () => {
    const validImplants = batchImplants.filter(i => i.patient_id && i.tipo_impianto && i.data_inserimento);
    if (validImplants.length === 0) {
      toast.error("Inserisci almeno un impianto completo (paziente, tipo, data)");
      return;
    }

    try {
      const response = await apiClient.post("/implants/batch", {
        implants: validImplants.map(i => ({
          patient_id: i.patient_id,
          tipo_impianto: i.tipo_impianto,
          data_inserimento: i.data_inserimento,
        }))
      });
      
      if (response.data.created > 0) {
        toast.success(`${response.data.created} schede impianto create con successo`);
      }
      if (response.data.errors > 0) {
        toast.warning(`${response.data.errors} impianti non creati`);
        response.data.error_details?.forEach(err => {
          toast.error(err.error);
        });
      }
      
      setBatchDialogOpen(false);
      setBatchImplants([{ patient_id: "", patient_name: "", tipo_impianto: "", data_inserimento: "" }]);
      fetchAllPatients();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Errore nella creazione degli impianti");
    }
  };

  const openStatusDialog = (patient, targetStatus, e) => {
    e.stopPropagation();
    setSelectedPatientForStatus(patient);
    setNewStatus(targetStatus);
    setStatusReason("");
    setStatusNotes("");
    setStatusDialogOpen(true);
  };

  const openDeleteDialog = (patient, e) => {
    e.stopPropagation();
    setSelectedPatientForDelete(patient);
    setDeleteDialogOpen(true);
  };

  const handleStatusChange = async () => {
    if (!selectedPatientForStatus) return;
    
    if (newStatus === "dimesso" && !statusReason) {
      toast.error("Seleziona una motivazione per la dimissione");
      return;
    }
    if (newStatus === "sospeso" && !statusNotes) {
      toast.error("Inserisci una nota per la sospensione");
      return;
    }
    if (newStatus === "dimesso" && statusReason === "altro" && !statusNotes) {
      toast.error("Inserisci una nota per specificare il motivo");
      return;
    }

    try {
      const updateData = { status: newStatus };
      
      if (newStatus === "dimesso") {
        updateData.discharge_reason = statusReason;
        updateData.discharge_notes = statusNotes;
      } else if (newStatus === "sospeso") {
        updateData.suspend_notes = statusNotes;
      }

      await apiClient.put(`/patients/${selectedPatientForStatus.id}`, updateData);
      
      const statusLabels = {
        in_cura: "ripreso in cura",
        dimesso: "dimesso",
        sospeso: "sospeso",
      };
      
      toast.success(`Paziente ${statusLabels[newStatus]}`);
      setStatusDialogOpen(false);
      fetchAllPatients();
    } catch (error) {
      toast.error("Errore nel cambio stato");
    }
  };

  const handleDeletePatient = async () => {
    if (!selectedPatientForDelete) return;
    
    try {
      await apiClient.delete(`/patients/${selectedPatientForDelete.id}`);
      toast.success("Paziente eliminato definitivamente");
      setDeleteDialogOpen(false);
      setSelectedPatientForDelete(null);
      fetchAllPatients();
    } catch (error) {
      toast.error("Errore nell'eliminazione del paziente");
    }
  };

  // Batch operations
  const togglePatientSelection = (patientId, e) => {
    e.stopPropagation();
    const newSelected = new Set(selectedPatients);
    if (newSelected.has(patientId)) {
      newSelected.delete(patientId);
    } else {
      newSelected.add(patientId);
    }
    setSelectedPatients(newSelected);
  };

  const selectAllFiltered = () => {
    const newSelected = new Set(filteredPatients.map(p => p.id));
    setSelectedPatients(newSelected);
  };

  const deselectAll = () => {
    setSelectedPatients(new Set());
  };

  const openBatchStatusDialog = (action) => {
    if (selectedPatients.size === 0) {
      toast.warning("Seleziona almeno un paziente");
      return;
    }
    setBatchAction(action);
    setNewStatus(action === 'suspend' ? 'sospeso' : action === 'resume' ? 'in_cura' : 'dimesso');
    setStatusReason("");
    setStatusNotes("");
    setBatchStatusDialogOpen(true);
  };

  const openBatchDeleteDialog = () => {
    if (selectedPatients.size === 0) {
      toast.warning("Seleziona almeno un paziente");
      return;
    }
    setBatchDeleteDialogOpen(true);
  };

  const handleBatchStatusChange = async () => {
    if (selectedPatients.size === 0) return;
    
    if (newStatus === "dimesso" && !statusReason) {
      toast.error("Seleziona una motivazione per la dimissione");
      return;
    }
    if (newStatus === "sospeso" && !statusNotes) {
      toast.error("Inserisci una nota per la sospensione");
      return;
    }

    try {
      const response = await apiClient.put("/patients/batch/status", {
        patient_ids: Array.from(selectedPatients),
        status: newStatus,
        discharge_reason: statusReason,
        discharge_notes: statusNotes,
        suspend_notes: statusNotes,
      });
      
      const statusLabels = {
        in_cura: "ripresi in cura",
        dimesso: "dimessi",
        sospeso: "sospesi",
      };
      
      toast.success(`${response.data.updated} pazienti ${statusLabels[newStatus]}`);
      if (response.data.errors > 0) {
        toast.warning(`${response.data.errors} pazienti non processati`);
      }
      
      setBatchStatusDialogOpen(false);
      setSelectedPatients(new Set());
      setSelectionMode(false);
      fetchAllPatients();
    } catch (error) {
      toast.error("Errore nel cambio stato");
    }
  };

  const handleBatchDelete = async () => {
    if (selectedPatients.size === 0) return;
    
    try {
      const response = await apiClient.post("/patients/batch/delete", {
        patient_ids: Array.from(selectedPatients)
      });
      
      toast.success(`${response.data.deleted} pazienti eliminati`);
      if (response.data.errors > 0) {
        toast.warning(`${response.data.errors} pazienti non eliminati`);
      }
      
      setBatchDeleteDialogOpen(false);
      setSelectedPatients(new Set());
      setSelectionMode(false);
      fetchAllPatients();
    } catch (error) {
      toast.error("Errore nell'eliminazione");
    }
  };

  const getTypeColor = (tipo) => {
    const type = PATIENT_TYPES.find(t => t.value === tipo);
    return type?.color || "bg-gray-100 text-gray-700";
  };

  const getInitials = (nome, cognome) => {
    return `${cognome?.charAt(0) || ""}${nome?.charAt(0) || ""}`.toUpperCase();
  };

  const getStatusActions = (patient) => {
    const currentStatus = patient.status;
    const actions = [];
    
    if (currentStatus !== "in_cura") {
      actions.push({
        label: "Riprendi in Cura",
        icon: Play,
        status: "in_cura",
        color: "text-green-600",
      });
    }
    if (currentStatus !== "sospeso") {
      actions.push({
        label: "Sospendi",
        icon: Pause,
        status: "sospeso",
        color: "text-orange-600",
      });
    }
    if (currentStatus !== "dimesso") {
      actions.push({
        label: "Dimetti",
        icon: UserX,
        status: "dimesso",
        color: "text-slate-600",
      });
    }
    
    return actions;
  };

  const getSelectedPatientNames = () => {
    const selected = filteredPatients.filter(p => selectedPatients.has(p.id));
    return selected.map(p => `${p.cognome} ${p.nome}`);
  };

  return (
    <div className="animate-fade-in" data-testid="pazienti-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Pazienti</h1>
          <p className="text-muted-foreground text-sm">
            Gestione cartelle cliniche
          </p>
        </div>

        <div className="flex gap-2">
          <Button 
            variant={selectionMode ? "secondary" : "outline"} 
            onClick={() => {
              setSelectionMode(!selectionMode);
              if (selectionMode) {
                setSelectedPatients(new Set());
              }
            }}
            data-testid="selection-mode-btn"
          >
            <ListChecks className="w-4 h-4 mr-2" />
            {selectionMode ? "Esci Selezione" : "Seleziona Multipli"}
          </Button>
          <Button variant="outline" onClick={() => setBatchDialogOpen(true)} data-testid="batch-create-btn">
            <Plus className="w-4 h-4 mr-2" />
            Aggiunta Multipla
          </Button>
          <Button onClick={() => setDialogOpen(true)} data-testid="create-patient-btn">
            <Plus className="w-4 h-4 mr-2" />
            Nuovo Paziente
          </Button>
        </div>
      </div>

      {/* Batch Action Bar */}
      {selectionMode && selectedPatients.size > 0 && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 mb-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-blue-700">
              {selectedPatients.size} selezionati
            </Badge>
            <Button variant="ghost" size="sm" onClick={selectAllFiltered}>
              Seleziona tutti ({filteredPatients.length})
            </Button>
            <Button variant="ghost" size="sm" onClick={deselectAll}>
              Deseleziona
            </Button>
          </div>
          <div className="flex gap-2 flex-wrap">
            {activeTab !== "in_cura" && (
              <Button size="sm" variant="outline" className="text-green-600 border-green-200 hover:bg-green-50" onClick={() => openBatchStatusDialog('resume')}>
                <Play className="w-4 h-4 mr-1" />
                Riprendi in Cura
              </Button>
            )}
            {activeTab !== "sospeso" && (
              <Button size="sm" variant="outline" className="text-orange-600 border-orange-200 hover:bg-orange-50" onClick={() => openBatchStatusDialog('suspend')}>
                <Pause className="w-4 h-4 mr-1" />
                Sospendi
              </Button>
            )}
            {activeTab !== "dimesso" && (
              <Button size="sm" variant="outline" className="text-slate-600 border-slate-200 hover:bg-slate-50" onClick={() => openBatchStatusDialog('discharge')}>
                <UserX className="w-4 h-4 mr-1" />
                Dimetti
              </Button>
            )}
            <Button size="sm" variant="outline" className="text-red-600 border-red-200 hover:bg-red-50" onClick={openBatchDeleteDialog}>
              <Trash2 className="w-4 h-4 mr-1" />
              Elimina
            </Button>
          </div>
        </div>
      )}

      {/* Patient Counters */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <Card 
          className={`border-emerald-200 cursor-pointer transition-all ${typeFilter === "PICC" ? "bg-emerald-100 ring-2 ring-emerald-500" : "bg-emerald-50/50 hover:bg-emerald-100/50"}`}
          onClick={() => setTypeFilter(typeFilter === "PICC" ? "all" : "PICC")}
        >
          <CardContent className="pt-4 pb-3 px-4">
            <div className="text-2xl font-bold text-emerald-600">
              {counts.picc_in_cura}
            </div>
            <p className="text-sm text-emerald-600/80 font-medium">PICC in cura</p>
          </CardContent>
        </Card>
        {!isVillaGinestre && (
          <Card 
            className={`border-blue-200 cursor-pointer transition-all ${typeFilter === "MED" ? "bg-blue-100 ring-2 ring-blue-500" : "bg-blue-50/50 hover:bg-blue-100/50"}`}
            onClick={() => setTypeFilter(typeFilter === "MED" ? "all" : "MED")}
          >
            <CardContent className="pt-4 pb-3 px-4">
              <div className="text-2xl font-bold text-blue-600">
                {counts.med_in_cura}
              </div>
              <p className="text-sm text-blue-600/80 font-medium">MED in cura</p>
            </CardContent>
          </Card>
        )}
        <Card className="border-green-200 bg-green-50/50">
          <CardContent className="pt-4 pb-3 px-4">
            <div className="text-2xl font-bold text-green-600">{counts.in_cura}</div>
            <p className="text-sm text-green-600/80 font-medium">Totale in cura</p>
          </CardContent>
        </Card>
        <Card className="border-gray-200 bg-gray-50/50">
          <CardContent className="pt-4 pb-3 px-4">
            <div className="text-2xl font-bold text-gray-600">{counts.dimesso + counts.sospeso}</div>
            <p className="text-sm text-gray-600/80 font-medium">Dimessi/Sospesi</p>
          </CardContent>
        </Card>
      </div>

      {/* Search and Filter */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            data-testid="patient-search-input"
            placeholder="Cerca per nome o cognome..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
        {!isVillaGinestre && (
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-muted-foreground" />
            <SimpleSelect
              value={typeFilter}
              onChange={setTypeFilter}
              options={typeFilterOptions}
              placeholder="Filtra per tipo"
              className="w-[180px]"
            />
          </div>
        )}
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList>
          <TabsTrigger value="in_cura" className="gap-2" data-testid="tab-in-cura">
            <Users className="w-4 h-4" />
            In Cura
            <Badge variant="secondary" className="ml-1">{counts.in_cura}</Badge>
          </TabsTrigger>
          <TabsTrigger value="sospeso" className="gap-2" data-testid="tab-sospeso">
            <Pause className="w-4 h-4" />
            Sospesi
            <Badge variant="secondary" className="ml-1">{counts.sospeso}</Badge>
          </TabsTrigger>
          <TabsTrigger value="dimesso" className="gap-2" data-testid="tab-dimesso">
            <UserCheck className="w-4 h-4" />
            Dimessi
            <Badge variant="secondary" className="ml-1">{counts.dimesso}</Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value={activeTab}>
          {loading ? (
            <div className="flex items-center justify-center h-64">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
            </div>
          ) : filteredPatients.length === 0 ? (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-12">
                <Users className="w-12 h-12 text-muted-foreground mb-4" />
                <p className="text-muted-foreground">
                  {typeFilter !== "all" 
                    ? `Nessun paziente ${typeFilter} trovato` 
                    : "Nessun paziente trovato"}
                </p>
                {activeTab === "in_cura" && typeFilter === "all" && (
                  <Button
                    variant="link"
                    onClick={() => setDialogOpen(true)}
                    className="mt-2"
                  >
                    Crea il primo paziente
                  </Button>
                )}
              </CardContent>
            </Card>
          ) : (
            <div className="grid gap-3">
              {filteredPatients.map((patient) => (
                <Card
                  key={patient.id}
                  data-testid={`patient-card-${patient.id}`}
                  className={`patient-card cursor-pointer hover:border-primary/50 ${selectedPatients.has(patient.id) ? 'ring-2 ring-blue-500 bg-blue-50' : ''}`}
                  onClick={() => selectionMode ? togglePatientSelection(patient.id, { stopPropagation: () => {} }) : navigate(`/pazienti/${patient.id}`)}
                >
                  {selectionMode && (
                    <div 
                      className="mr-2 flex items-center"
                      onClick={(e) => togglePatientSelection(patient.id, e)}
                    >
                      {selectedPatients.has(patient.id) ? (
                        <CheckSquare className="w-5 h-5 text-blue-600" />
                      ) : (
                        <Square className="w-5 h-5 text-gray-400" />
                      )}
                    </div>
                  )}
                  <div className="patient-avatar">
                    {getInitials(patient.nome, patient.cognome)}
                  </div>
                  <div className="patient-info">
                    <div className="patient-name">
                      {patient.cognome} {patient.nome}
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge className={`patient-type ${getTypeColor(patient.tipo)}`}>
                        {patient.tipo === "PICC_MED" ? "PICC + MED" : patient.tipo}
                      </Badge>
                      {patient.discharge_reason && activeTab === "dimesso" && (
                        <span className="text-xs text-muted-foreground">
                          ({patient.discharge_reason === "guarito" ? "Guarito" : 
                            patient.discharge_reason === "adi" ? "ADI" : "Altro"})
                        </span>
                      )}
                    </div>
                  </div>
                  
                  {!selectionMode && (
                    <>
                      {/* Status Actions Dropdown */}
                      <SimpleDropdown
                        trigger={
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreVertical className="w-4 h-4" />
                          </Button>
                        }
                      >
                        {getStatusActions(patient).map((action) => (
                          <DropdownItem
                            key={action.status}
                            onClick={(e) => openStatusDialog(patient, action.status, e)}
                            icon={action.icon}
                            className={action.color}
                          >
                            {action.label}
                          </DropdownItem>
                        ))}
                        <DropdownSeparator />
                        <DropdownItem
                          onClick={(e) => {
                            e.stopPropagation();
                            navigate(`/pazienti/${patient.id}`);
                          }}
                          icon={ChevronRight}
                        >
                          Apri Cartella
                        </DropdownItem>
                        <DropdownSeparator />
                        <DropdownItem
                          onClick={(e) => openDeleteDialog(patient, e)}
                          icon={Trash2}
                          className="text-destructive"
                        >
                          Elimina Definitivamente
                        </DropdownItem>
                      </SimpleDropdown>
                      
                      <ChevronRight className="w-5 h-5 text-muted-foreground" />
                    </>
                  )}
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Create Patient Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nuovo Paziente</DialogTitle>
            <DialogDescription>
              Inserisci i dati del nuovo paziente per creare la cartella clinica
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="cognome">Cognome *</Label>
                <Input
                  id="cognome"
                  data-testid="new-patient-cognome"
                  placeholder="Cognome"
                  value={newPatient.cognome}
                  onChange={(e) =>
                    setNewPatient({ ...newPatient, cognome: e.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="nome">Nome *</Label>
                <Input
                  id="nome"
                  data-testid="new-patient-nome"
                  placeholder="Nome"
                  value={newPatient.nome}
                  onChange={(e) =>
                    setNewPatient({ ...newPatient, nome: e.target.value })
                  }
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>Tipologia *</Label>
              <SimpleSelect
                value={newPatient.tipo}
                onChange={(value) => setNewPatient({ ...newPatient, tipo: value })}
                options={patientTypeOptions}
                placeholder="Seleziona tipologia"
              />
            </div>

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>
                Annulla
              </Button>
              <Button
                onClick={handleCreatePatient}
                data-testid="confirm-create-patient-btn"
              >
                Crea e Apri Cartella
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Batch Create Dialog - Aggiunta Multipla */}
      <Dialog open={batchDialogOpen} onOpenChange={(open) => {
        setBatchDialogOpen(open);
        if (!open) {
          setBatchTab("pazienti");
        }
      }}>
        <DialogContent className="sm:max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Aggiunta Multipla</DialogTitle>
            <DialogDescription>
              Scegli se aggiungere nuovi pazienti o schede impianto per pazienti PICC esistenti.
            </DialogDescription>
          </DialogHeader>

          {/* Tab Selection */}
          <div className="flex gap-2 mb-4">
            <Button
              variant={batchTab === "pazienti" ? "default" : "outline"}
              onClick={() => setBatchTab("pazienti")}
              className="flex-1"
            >
              <Users className="w-4 h-4 mr-2" />
              Aggiungi Pazienti
            </Button>
            <Button
              variant={batchTab === "impianti" ? "default" : "outline"}
              onClick={() => setBatchTab("impianti")}
              className={`flex-1 ${batchTab === "impianti" ? "bg-emerald-600 hover:bg-emerald-700" : "text-emerald-600 border-emerald-200 hover:bg-emerald-50"}`}
            >
              <Plus className="w-4 h-4 mr-2" />
              Aggiungi Impianti
            </Button>
          </div>

          {/* Tab Content - Pazienti */}
          {batchTab === "pazienti" && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Inserisci i dati di più pazienti contemporaneamente. Per pazienti PICC puoi specificare anche il tipo di impianto e la data di inserimento.
              </p>
              {batchPatients.map((patient, index) => (
                <div key={index} className="p-3 bg-gray-50 rounded-lg space-y-2">
                  <div className="flex gap-2 items-start">
                    <div className="flex-1 grid grid-cols-3 gap-2">
                      <Input
                        placeholder="Cognome"
                        value={patient.cognome}
                        onChange={(e) => updateBatchPatient(index, 'cognome', e.target.value)}
                      />
                      <Input
                        placeholder="Nome"
                        value={patient.nome}
                        onChange={(e) => updateBatchPatient(index, 'nome', e.target.value)}
                      />
                    <SimpleSelect
                      value={patient.tipo}
                      onChange={(value) => {
                        updateBatchPatient(index, 'tipo', value);
                        // Reset tipo impianto se non è PICC
                        if (value !== 'PICC' && value !== 'PICC_MED') {
                          updateBatchPatient(index, 'tipo_impianto', '');
                          updateBatchPatient(index, 'data_inserimento_impianto', '');
                        }
                      }}
                      options={patientTypeOptions}
                      placeholder="Tipo"
                    />
                  </div>
                  {batchPatients.length > 1 && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-10 w-10 text-red-500"
                      onClick={() => removeBatchPatientRow(index)}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  )}
                </div>
                
                {/* Campi aggiuntivi per PICC */}
                {(patient.tipo === 'PICC' || patient.tipo === 'PICC_MED') && (
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-gray-200 mt-2">
                    <div className="space-y-1">
                      <Label className="text-xs text-gray-600">Tipo Impianto</Label>
                      <SimpleSelect
                        value={patient.tipo_impianto || ''}
                        onChange={(value) => updateBatchPatient(index, 'tipo_impianto', value)}
                        options={tipoImpiantoOptions}
                        placeholder="Seleziona tipo"
                        className="w-full"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs text-gray-600">Data Inserimento Impianto</Label>
                      <Input
                        type="date"
                        value={patient.data_inserimento_impianto || ''}
                        onChange={(e) => updateBatchPatient(index, 'data_inserimento_impianto', e.target.value)}
                        className="h-10"
                      />
                    </div>
                  </div>
                )}
              </div>
            ))}

            <Button variant="outline" onClick={addBatchPatientRow} className="w-full">
              <Plus className="w-4 h-4 mr-2" />
              Aggiungi Riga
            </Button>

            <div className="flex justify-end gap-2 pt-4 border-t">
              <Button variant="outline" onClick={() => {
                setBatchDialogOpen(false);
                setBatchPatients([{ nome: "", cognome: "", tipo: "", tipo_impianto: "", data_inserimento_impianto: "" }]);
              }}>
                Annulla
              </Button>
              <Button onClick={handleBatchCreate}>
                Crea {batchPatients.filter(p => p.nome && p.cognome && p.tipo).length} Pazienti
              </Button>
            </div>
          </div>
          )}

          {/* Tab Content - Impianti */}
          {batchTab === "impianti" && (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">
                Cerca pazienti PICC esistenti e aggiungi le schede impianto.
              </p>
              {batchImplants.map((implant, index) => (
                <div key={index} className="p-3 bg-emerald-50 rounded-lg space-y-3">
                  <div className="flex gap-2 items-start">
                    <div className="flex-1 space-y-2">
                      {/* Ricerca paziente */}
                      <div className="relative">
                        <Label className="text-xs text-gray-600">Paziente PICC</Label>
                        {implant.patient_id ? (
                          <div className="flex items-center gap-2">
                            <Input
                              value={implant.patient_name}
                              readOnly
                              className="bg-white"
                            />
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                updateBatchImplant(index, 'patient_id', '');
                                updateBatchImplant(index, 'patient_name', '');
                              }}
                            >
                              <X className="w-4 h-4" />
                            </Button>
                          </div>
                        ) : (
                          <>
                            <Input
                              placeholder="Cerca paziente PICC..."
                              onChange={(e) => {
                                updateBatchImplant(index, 'patient_name', e.target.value);
                                searchPiccPatients(e.target.value, index);
                              }}
                              value={implant.patient_name}
                            />
                            {activeSearchIndex === index && piccPatientResults.length > 0 && (
                              <div className="absolute z-50 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                                {piccPatientResults.map((patient) => (
                                  <button
                                    key={patient.id}
                                    className="w-full px-3 py-2 text-left hover:bg-gray-100 flex justify-between items-center"
                                    onClick={() => selectPiccPatient(patient, index)}
                                  >
                                    <span>{patient.cognome} {patient.nome}</span>
                                    <Badge variant="outline" className="text-xs">{patient.tipo}</Badge>
                                  </button>
                                ))}
                              </div>
                            )}
                            {activeSearchIndex === index && searchingPicc && (
                              <div className="absolute z-50 w-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg p-3 text-center text-sm text-gray-500">
                                Ricerca in corso...
                              </div>
                            )}
                          </>
                        )}
                      </div>
                      
                      {/* Tipo impianto e data */}
                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <Label className="text-xs text-gray-600">Tipo Impianto</Label>
                          <SimpleSelect
                            value={implant.tipo_impianto || ''}
                            onChange={(value) => updateBatchImplant(index, 'tipo_impianto', value)}
                            options={tipoImpiantoOptions}
                            placeholder="Seleziona tipo"
                          />
                        </div>
                        <div className="space-y-1">
                          <Label className="text-xs text-gray-600">Data Inserimento</Label>
                          <Input
                            type="date"
                            value={implant.data_inserimento || ''}
                            onChange={(e) => updateBatchImplant(index, 'data_inserimento', e.target.value)}
                            className="h-10"
                          />
                        </div>
                      </div>
                    </div>
                    
                    {batchImplants.length > 1 && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-10 w-10 text-red-500 mt-5"
                        onClick={() => removeBatchImplantRow(index)}
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    )}
                  </div>
                </div>
              ))}

              <Button variant="outline" onClick={addBatchImplantRow} className="w-full">
                <Plus className="w-4 h-4 mr-2" />
                Aggiungi Riga
              </Button>

              <div className="flex justify-end gap-2 pt-4 border-t">
                <Button variant="outline" onClick={() => {
                  setBatchDialogOpen(false);
                  setBatchImplants([{ patient_id: "", patient_name: "", tipo_impianto: "", data_inserimento: "" }]);
                  setPiccPatientResults([]);
                }}>
                  Annulla
                </Button>
                <Button 
                  onClick={handleBatchImplantsCreate}
                  className="bg-emerald-600 hover:bg-emerald-700"
                >
                  Crea {batchImplants.filter(i => i.patient_id && i.tipo_impianto && i.data_inserimento).length} Impianti
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Status Change Dialog */}
      <Dialog open={statusDialogOpen} onOpenChange={setStatusDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {newStatus === "in_cura" && "Riprendi in Cura"}
              {newStatus === "dimesso" && "Dimetti Paziente"}
              {newStatus === "sospeso" && "Sospendi Paziente"}
            </DialogTitle>
            <DialogDescription>
              {selectedPatientForStatus && (
                <span className="font-medium">
                  {selectedPatientForStatus.cognome} {selectedPatientForStatus.nome}
                </span>
              )}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {newStatus === "in_cura" && (
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                <p className="text-sm text-green-800">
                  Il paziente verrà riportato in stato &quot;In Cura&quot;. Lo storico delle dimissioni/sospensioni precedenti verrà conservato.
                </p>
              </div>
            )}

            {newStatus === "dimesso" && (
              <div className="space-y-2">
                <Label>Motivazione *</Label>
                <SimpleSelect
                  value={statusReason}
                  onChange={setStatusReason}
                  options={dischargeReasonOptions}
                  placeholder="Seleziona motivazione"
                />
              </div>
            )}

            {(newStatus === "sospeso" || (newStatus === "dimesso" && statusReason)) && (
              <div className="space-y-2">
                <Label>
                  {newStatus === "sospeso" ? "Motivo Sospensione *" : "Note"}
                  {newStatus === "dimesso" && statusReason === "altro" && " *"}
                </Label>
                <Textarea
                  value={statusNotes}
                  onChange={(e) => setStatusNotes(e.target.value)}
                  placeholder={
                    newStatus === "sospeso"
                      ? "Inserisci il motivo della sospensione..."
                      : "Note aggiuntive..."
                  }
                  rows={3}
                />
              </div>
            )}

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setStatusDialogOpen(false)}>
                Annulla
              </Button>
              <Button onClick={handleStatusChange}>
                Conferma
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Batch Status Change Dialog */}
      <Dialog open={batchStatusDialogOpen} onOpenChange={setBatchStatusDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {newStatus === "in_cura" && "Riprendi in Cura"}
              {newStatus === "dimesso" && "Dimetti Pazienti"}
              {newStatus === "sospeso" && "Sospendi Pazienti"}
            </DialogTitle>
            <DialogDescription>
              {selectedPatients.size} pazienti selezionati
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="max-h-32 overflow-y-auto bg-gray-50 rounded-lg p-2">
              {getSelectedPatientNames().map((name, i) => (
                <div key={i} className="text-sm py-1">• {name}</div>
              ))}
            </div>

            {newStatus === "in_cura" && (
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg">
                <p className="text-sm text-green-800">
                  I pazienti verranno riportati in stato &quot;In Cura&quot;.
                </p>
              </div>
            )}

            {newStatus === "dimesso" && (
              <div className="space-y-2">
                <Label>Motivazione *</Label>
                <SimpleSelect
                  value={statusReason}
                  onChange={setStatusReason}
                  options={dischargeReasonOptions}
                  placeholder="Seleziona motivazione"
                />
              </div>
            )}

            {(newStatus === "sospeso" || (newStatus === "dimesso" && statusReason)) && (
              <div className="space-y-2">
                <Label>
                  {newStatus === "sospeso" ? "Motivo Sospensione *" : "Note"}
                </Label>
                <Textarea
                  value={statusNotes}
                  onChange={(e) => setStatusNotes(e.target.value)}
                  placeholder={
                    newStatus === "sospeso"
                      ? "Inserisci il motivo della sospensione..."
                      : "Note aggiuntive..."
                  }
                  rows={3}
                />
              </div>
            )}

            <div className="flex justify-end gap-2 pt-4">
              <Button variant="outline" onClick={() => setBatchStatusDialogOpen(false)}>
                Annulla
              </Button>
              <Button onClick={handleBatchStatusChange}>
                Conferma ({selectedPatients.size} pazienti)
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Eliminare definitivamente questo paziente?</AlertDialogTitle>
            <AlertDialogDescription>
              {selectedPatientForDelete && (
                <>
                  Stai per eliminare <strong>{selectedPatientForDelete.cognome} {selectedPatientForDelete.nome}</strong>.
                  <br /><br />
                  Questa azione è irreversibile e cancellerà tutti i dati, le schede e lo storico del paziente.
                </>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Annulla</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeletePatient}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Elimina Definitivamente
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Batch Delete Confirmation Dialog */}
      <AlertDialog open={batchDeleteDialogOpen} onOpenChange={setBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Eliminare definitivamente {selectedPatients.size} pazienti?</AlertDialogTitle>
            <AlertDialogDescription>
              <div className="max-h-32 overflow-y-auto bg-gray-50 rounded-lg p-2 my-2">
                {getSelectedPatientNames().map((name, i) => (
                  <div key={i} className="text-sm py-1">• {name}</div>
                ))}
              </div>
              Questa azione è irreversibile e cancellerà tutti i dati, le schede e lo storico dei pazienti.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Annulla</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBatchDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Elimina {selectedPatients.size} Pazienti
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
