from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, UploadFile, File, Form, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import List, Optional, Dict, Any, Union
import uuid
from datetime import datetime, timezone, date, timedelta
import jwt
import bcrypt
from enum import Enum
import base64
import io
import zipfile
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection with better timeout handling for Atlas
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(
    mongo_url,
    serverSelectionTimeoutMS=30000,
    connectTimeoutMS=30000,
    socketTimeoutMS=30000,
    maxPoolSize=10,
    retryWrites=True,
    retryReads=True
)
db = client[os.environ['DB_NAME']]

# JWT Settings
JWT_SECRET = os.environ.get('JWT_SECRET', 'ambulatorio-infermieristico-secret-key-2024')
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

security = HTTPBearer()

# Create the main app
app = FastAPI(title="Ambulatorio Infermieristico API")

# Health check endpoint for Kubernetes - MUST be at root level, not under /api
@app.get("/health")
async def health_check():
    """Health check endpoint for Kubernetes liveness/readiness probes"""
    return {"status": "healthy"}

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# ============== ENUMS ==============
class PatientType(str, Enum):
    PICC = "PICC"
    MED = "MED"
    PICC_MED = "PICC_MED"

class PatientStatus(str, Enum):
    IN_CURA = "in_cura"
    DIMESSO = "dimesso"
    SOSPESO = "sospeso"

class DischargeReason(str, Enum):
    GUARITO = "guarito"
    ADI = "adi"
    ALTRO = "altro"

class Ambulatorio(str, Enum):
    PTA_CENTRO = "pta_centro"
    VILLA_GINESTRE = "villa_ginestre"

# ============== MODELS ==============
class UserLogin(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: str
    username: str
    ambulatori: List[str]

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

class PatientCreate(BaseModel):
    nome: str
    cognome: str
    tipo: PatientType
    ambulatorio: Ambulatorio
    data_nascita: Optional[str] = None
    codice_fiscale: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    medico_base: Optional[str] = None
    anamnesi: Optional[str] = None
    terapia_in_atto: Optional[str] = None
    allergie: Optional[str] = None
    # Campi per impianto PICC (opzionali, usati nella creazione batch)
    tipo_impianto: Optional[str] = None  # picc, picc_port, midline
    data_inserimento_impianto: Optional[str] = None  # YYYY-MM-DD

class PatientUpdate(BaseModel):
    nome: Optional[str] = None
    cognome: Optional[str] = None
    tipo: Optional[PatientType] = None
    data_nascita: Optional[str] = None
    codice_fiscale: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    medico_base: Optional[str] = None
    anamnesi: Optional[str] = None
    terapia_in_atto: Optional[str] = None
    allergie: Optional[str] = None
    status: Optional[PatientStatus] = None
    discharge_reason: Optional[str] = None
    discharge_notes: Optional[str] = None
    suspend_notes: Optional[str] = None
    lesion_markers: Optional[List[Dict[str, Any]]] = None

# Helper function to generate unique patient code
def generate_patient_code(nome: str, cognome: str) -> str:
    """Generate unique patient code like 'm234h' based on name"""
    import random
    import string
    # Take first letter of cognome lowercase
    prefix = cognome[0].lower() if cognome else 'x'
    # Generate 3 random digits
    digits = ''.join(random.choices(string.digits, k=3))
    # Add 1 random letter
    suffix = random.choice(string.ascii_lowercase)
    return f"{prefix}{digits}{suffix}"

class Patient(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice_paziente: str = ""  # Codice univoco paziente (es. m234h)
    nome: str
    cognome: str
    tipo: PatientType
    ambulatorio: Ambulatorio
    status: PatientStatus = PatientStatus.IN_CURA
    data_nascita: Optional[str] = None
    codice_fiscale: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None
    medico_base: Optional[str] = None
    anamnesi: Optional[str] = None
    terapia_in_atto: Optional[str] = None
    allergie: Optional[str] = None
    lesion_markers: List[Dict[str, Any]] = []
    discharge_reason: Optional[str] = None
    discharge_notes: Optional[str] = None
    suspend_notes: Optional[str] = None
    scheda_med_counter: int = 0  # Counter for MED schede
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Prestazioni
class PrestazionePICC(str, Enum):
    MEDICAZIONE_SEMPLICE = "medicazione_semplice"
    IRRIGAZIONE_CATETERE = "irrigazione_catetere"

class PrestazioneMED(str, Enum):
    MEDICAZIONE_SEMPLICE = "medicazione_semplice"
    FASCIATURA_SEMPLICE = "fasciatura_semplice"
    INIEZIONE_TERAPEUTICA = "iniezione_terapeutica"
    CATETERE_VESCICALE = "catetere_vescicale"

class AppointmentCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    data: str  # YYYY-MM-DD
    ora: str   # HH:MM
    tipo: str  # PICC or MED
    prestazioni: List[str]
    note: Optional[str] = None
    stato: Optional[str] = "da_fare"  # da_fare, effettuato, non_presentato

class Appointment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    patient_nome: Optional[str] = None
    patient_cognome: Optional[str] = None
    ambulatorio: Ambulatorio
    data: str
    ora: str
    tipo: str
    prestazioni: List[str]
    note: Optional[str] = None
    stato: str = "da_fare"  # da_fare, effettuato, non_presentato
    completed: bool = False  # kept for backward compatibility
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Scheda Medicazione MED
class SchedaMedicazioneMEDCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    data_compilazione: str
    fondo: List[str] = []  # granuleggiante, fibrinoso, necrotico, infetto, biofilmato
    margini: List[str] = []  # attivi, piantati, in_estensione, a_scogliera
    cute_perilesionale: List[str] = []  # integra, secca, arrossata, macerata, ipercheratosica
    essudato_quantita: Optional[str] = None  # assente, moderato, abbondante
    essudato_tipo: List[str] = []  # sieroso, ematico, infetto
    medicazione: str = "La lesione è stata trattata seguendo le 4 fasi del Wound Hygiene:\nDetersione con Prontosan\nDebridement e Riattivazione dei margini\nMedicazione: "
    prossimo_cambio: Optional[str] = None
    firma: Optional[str] = None
    foto_ids: List[str] = []

# Helper to generate unique scheda code
def generate_scheda_code(data_compilazione: str) -> str:
    """Generate unique code for scheda: MED-DDMMYY-XXXX"""
    try:
        dt = datetime.strptime(data_compilazione, "%Y-%m-%d")
        date_part = dt.strftime("%d%m%y")
    except:
        date_part = datetime.now().strftime("%d%m%y")
    random_part = uuid.uuid4().hex[:4].upper()
    return f"MED-{date_part}-{random_part}"

class SchedaMedicazioneMED(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    codice: str = Field(default="")  # Codice identificativo univoco
    patient_id: str
    ambulatorio: Ambulatorio
    data_compilazione: str
    fondo: List[str] = []
    margini: List[str] = []
    cute_perilesionale: List[str] = []
    essudato_quantita: Optional[str] = None
    essudato_tipo: List[str] = []
    medicazione: str
    prossimo_cambio: Optional[str] = None
    firma: Optional[str] = None
    foto_ids: List[str] = []
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Scheda Impianto PICC - Nuova struttura completa
class SchedaImpiantoPICCCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    scheda_type: str = "semplificata"  # semplificata o completa
    # Header
    presidio_ospedaliero: Optional[str] = None
    codice: Optional[str] = None
    unita_operativa: Optional[str] = None
    data_presa_carico: Optional[str] = None
    cartella_clinica: Optional[str] = None
    # Sezione Catetere Già Presente
    catetere_presente: bool = False
    catetere_presente_tipo: Optional[str] = None
    catetere_presente_struttura: Optional[str] = None
    catetere_presente_data: Optional[str] = None
    catetere_presente_ora: Optional[str] = None
    catetere_presente_modalita: Optional[str] = None
    catetere_presente_rx: Optional[bool] = None
    catetere_da_sostituire: Optional[bool] = None
    # Sezione Impianto Catetere
    tipo_catetere: Optional[str] = None
    posizionamento_cvc: Optional[str] = None
    posizionamento_cvc_altro: Optional[str] = None
    braccio: Optional[str] = None
    vena: Optional[str] = None
    exit_site_cm: Optional[str] = None
    tunnelizzazione: Optional[bool] = False
    tunnelizzazione_note: Optional[str] = None
    valutazione_sito: Optional[bool] = None
    ecoguidato: Optional[bool] = None
    igiene_mani: Optional[bool] = None
    precauzioni_barriera: Optional[bool] = None
    disinfezione: Optional[List[str]] = []
    sutureless_device: Optional[bool] = None
    medicazione_trasparente: Optional[bool] = None
    medicazione_occlusiva: Optional[bool] = None
    controllo_rx: Optional[bool] = None
    controllo_ecg: Optional[bool] = None
    modalita: Optional[str] = None
    motivazione: List[str] = []
    motivazione_altro: Optional[str] = None
    data_posizionamento: Optional[str] = None
    operatore: Optional[str] = None
    allegati: List[str] = []
    # Legacy fields for backward compatibility
    data_impianto: Optional[str] = None
    sede: Optional[str] = None
    disinfettante: Optional[str] = None
    note: Optional[str] = None

class SchedaImpiantoPICC(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    ambulatorio: Ambulatorio
    scheda_type: str = "semplificata"  # semplificata o completa
    # Header
    presidio_ospedaliero: Optional[str] = None
    codice: Optional[str] = None
    unita_operativa: Optional[str] = None
    data_presa_carico: Optional[str] = None
    cartella_clinica: Optional[str] = None
    # Sezione Catetere Già Presente
    catetere_presente: bool = False
    catetere_presente_tipo: Optional[str] = None
    catetere_presente_struttura: Optional[str] = None
    catetere_presente_data: Optional[str] = None
    catetere_presente_ora: Optional[str] = None
    catetere_presente_modalita: Optional[str] = None
    catetere_presente_rx: Optional[bool] = None
    catetere_da_sostituire: Optional[bool] = None
    # Sezione Impianto Catetere
    tipo_catetere: Optional[str] = None
    posizionamento_cvc: Optional[str] = None
    posizionamento_cvc_altro: Optional[str] = None
    braccio: Optional[str] = None
    vena: Optional[str] = None
    exit_site_cm: Optional[str] = None
    tunnelizzazione: Optional[bool] = False
    tunnelizzazione_note: Optional[str] = None
    valutazione_sito: Optional[bool] = None
    ecoguidato: Optional[bool] = None
    igiene_mani: Optional[bool] = None
    precauzioni_barriera: Optional[bool] = None
    disinfezione: Optional[List[str]] = []
    sutureless_device: Optional[bool] = None
    medicazione_trasparente: Optional[bool] = None
    medicazione_occlusiva: Optional[bool] = None
    controllo_rx: Optional[bool] = None
    controllo_ecg: Optional[bool] = None
    modalita: Optional[str] = None
    motivazione: Optional[List[str]] = []
    motivazione_altro: Optional[str] = None
    data_posizionamento: Optional[str] = None
    operatore: Optional[str] = None
    allegati: Optional[List[str]] = []
    # Legacy fields
    data_impianto: Optional[str] = None
    sede: Optional[str] = None
    disinfettante: Optional[str] = None
    note: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
    @field_validator('motivazione', 'disinfezione', 'allegati', mode='before')
    @classmethod
    def convert_none_to_list(cls, v):
        return v if v is not None else []

# Scheda Gestione Mensile PICC
class SchedaGestionePICCCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    mese: str  # YYYY-MM
    giorni: Dict[str, Dict[str, Any]] = {}  # {1: {lavaggio_mani: true, ...}, 2: {...}}
    note: Optional[str] = None

class SchedaGestionePICC(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    ambulatorio: Ambulatorio
    mese: str
    giorni: Dict[str, Dict[str, Any]] = {}
    note: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Photo / Attachment
class PhotoCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    tipo: str  # MED, PICC, MED_SCHEDA
    descrizione: Optional[str] = None
    data: str
    file_type: Optional[str] = "image"  # image, pdf, word, excel
    original_name: Optional[str] = None
    scheda_med_id: Optional[str] = None  # Link to specific scheda MED

class Photo(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    ambulatorio: Ambulatorio
    tipo: str
    descrizione: Optional[str] = None
    data: str
    image_data: str  # Base64
    file_type: Optional[str] = "image"  # image, pdf, word, excel
    original_name: Optional[str] = None
    mime_type: Optional[str] = None
    scheda_med_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

# Document Templates
class DocumentTemplate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    nome: str
    categoria: str  # PICC or MED
    tipo_file: str  # pdf, word
    url: str

# Statistics
class StatisticsQuery(BaseModel):
    ambulatorio: Ambulatorio
    tipo: Optional[str] = None  # PICC, MED or None for all
    anno: int
    mese: Optional[int] = None

# ============== USERS DATA ==============
USERS = {
    "Domenico": {
        "password": "infermiere",
        "ambulatori": ["pta_centro", "villa_ginestre"]
    },
    "Antonella": {
        "password": "infermiere",
        "ambulatori": ["pta_centro", "villa_ginestre"]
    },
    "Giovanna": {
        "password": "infermiere",
        "ambulatori": ["pta_centro"]
    },
    "Oriana": {
        "password": "infermiere",
        "ambulatori": ["pta_centro"]
    },
    "G.Domenico": {
        "password": "infermiere",
        "ambulatori": ["pta_centro"]
    }
}

# Document templates
DOCUMENT_TEMPLATES = [
    # MED Documents
    {"id": "consent_med", "nome": "Consenso Informato MED", "categoria": "MED", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_f548c735-b113-437f-82ec-c0afbf122c8d/artifacts/k3jcaxa4_CONSENSO_INFORMATO.pdf"},
    {"id": "scheda_mmg", "nome": "Scheda MMG", "categoria": "MED", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_f548c735-b113-437f-82ec-c0afbf122c8d/artifacts/8bonfflf_SCHEDA_MMG.pdf"},
    {"id": "anagrafica_med", "nome": "Anagrafica/Anamnesi MED", "categoria": "MED", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_f548c735-b113-437f-82ec-c0afbf122c8d/artifacts/txx60tb0_anagrafica%20med.jpg"},
    {"id": "scheda_medicazione_med", "nome": "Scheda Medicazione MED", "categoria": "MED", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_f548c735-b113-437f-82ec-c0afbf122c8d/artifacts/nzkb51vc_medicazione%20med.jpg"},
    # PICC Documents
    {"id": "consent_picc_1", "nome": "Consenso Generico Processi Clinico-Assistenziali", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_medhub-38/artifacts/ysusww7f_CONSENSO%20GENERICO%20AI%20PROCESSI%20CLINICO.ASSISTENZIALI%20ORDINARI%201.pdf"},
    {"id": "consent_picc_2", "nome": "Consenso Informato PICC e Midline", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_medhub-38/artifacts/siz46bgw_CONSENSO%20INFORMATO%20PICC%20E%20MIDLINE.pdf"},
    {"id": "brochure_picc_port", "nome": "Brochure PICC Port", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_medhub-38/artifacts/cein282q_Picc%20Port.pdf"},
    {"id": "brochure_picc", "nome": "Brochure PICC", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_medhub-38/artifacts/kk882djy_Picc.pdf"},
    {"id": "scheda_impianto_picc", "nome": "Scheda Impianto e Gestione AV", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_medhub-38/artifacts/sbw1iws9_Sch%20Impianto%20Gestione%20AV%20NEW.pdf"},
    {"id": "scheda_impianto_pdf", "nome": "Scheda Impianto", "categoria": "PICC", "tipo_file": "pdf", "url": "https://customer-assets.emergentagent.com/job_docucare-6/artifacts/3c52ewuw_Scheda%20Impianto.pdf"},
]

# Italian holidays for Palermo
def get_holidays(year: int) -> List[str]:
    holidays = [
        f"{year}-01-01",  # Capodanno
        f"{year}-01-06",  # Epifania
        f"{year}-04-25",  # Liberazione
        f"{year}-05-01",  # Festa del Lavoro
        f"{year}-06-02",  # Festa della Repubblica
        f"{year}-07-15",  # Santa Rosalia (Palermo)
        f"{year}-08-15",  # Ferragosto
        f"{year}-11-01",  # Ognissanti
        f"{year}-12-08",  # Immacolata
        f"{year}-12-25",  # Natale
        f"{year}-12-26",  # Santo Stefano
    ]
    # Easter calculation (simplified - would need proper algorithm for accuracy)
    # Adding approximate Easter dates for 2026-2030
    easter_dates = {
        2026: "2026-04-05",
        2027: "2027-03-28",
        2028: "2028-04-16",
        2029: "2029-04-01",
        2030: "2030-04-21",
    }
    if year in easter_dates:
        easter = easter_dates[year]
        holidays.append(easter)
        # Pasquetta (Easter Monday)
        easter_date = datetime.strptime(easter, "%Y-%m-%d")
        pasquetta = easter_date + timedelta(days=1)
        holidays.append(pasquetta.strftime("%Y-%m-%d"))
    return holidays

# ============== AUTH HELPERS ==============
def create_token(username: str, ambulatori: List[str]) -> str:
    payload = {
        "sub": username,
        "ambulatori": ambulatori,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token scaduto")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token non valido")

# ============== AUTH ROUTES ==============
@api_router.post("/auth/login", response_model=TokenResponse)
async def login(data: UserLogin):
    user = USERS.get(data.username)
    if not user or user["password"] != data.password:
        raise HTTPException(status_code=401, detail="Credenziali non valide")
    
    token = create_token(data.username, user["ambulatori"])
    return TokenResponse(
        access_token=token,
        user=UserResponse(
            id=data.username.lower().replace(".", "_"),
            username=data.username,
            ambulatori=user["ambulatori"]
        )
    )

@api_router.get("/auth/me", response_model=UserResponse)
async def get_current_user(payload: dict = Depends(verify_token)):
    username = payload["sub"]
    user = USERS.get(username)
    if not user:
        raise HTTPException(status_code=404, detail="Utente non trovato")
    return UserResponse(
        id=username.lower().replace(".", "_"),
        username=username,
        ambulatori=user["ambulatori"]
    )

# ============== PATIENTS ROUTES ==============
@api_router.post("/patients", response_model=Patient, status_code=201)
async def create_patient(data: PatientCreate, payload: dict = Depends(verify_token)):
    # Check ambulatorio access
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Villa Ginestre only allows PICC
    if data.ambulatorio == Ambulatorio.VILLA_GINESTRE and data.tipo != PatientType.PICC:
        raise HTTPException(status_code=400, detail="Villa delle Ginestre gestisce solo pazienti PICC")
    
    # Generate unique patient code
    codice_paziente = generate_patient_code(data.nome, data.cognome)
    # Ensure uniqueness
    while await db.patients.find_one({"codice_paziente": codice_paziente}):
        codice_paziente = generate_patient_code(data.nome, data.cognome)
    
    patient_data = data.model_dump()
    patient_data["codice_paziente"] = codice_paziente
    patient_data["scheda_med_counter"] = 0
    patient = Patient(**patient_data)
    doc = patient.model_dump()
    await db.patients.insert_one(doc)
    return patient

@api_router.get("/patients", response_model=List[Patient])
async def get_patients(
    ambulatorio: Ambulatorio,
    status: Optional[PatientStatus] = None,
    tipo: Optional[PatientType] = None,
    search: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {"ambulatorio": ambulatorio.value}
    if status:
        query["status"] = status.value
    if tipo:
        query["tipo"] = tipo.value
    if search:
        query["$or"] = [
            {"nome": {"$regex": search, "$options": "i"}},
            {"cognome": {"$regex": search, "$options": "i"}}
        ]
    
    patients = await db.patients.find(query, {"_id": 0}).sort("cognome", 1).to_list(1000)
    return patients

@api_router.get("/patients/{patient_id}", response_model=Patient)
async def get_patient(patient_id: str, payload: dict = Depends(verify_token)):
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    if patient["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    return patient

@api_router.put("/patients/{patient_id}", response_model=Patient)
async def update_patient(patient_id: str, data: PatientUpdate, payload: dict = Depends(verify_token)):
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    if patient["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    await db.patients.update_one({"id": patient_id}, {"$set": update_data})
    updated = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    return updated

@api_router.delete("/patients/{patient_id}")
async def delete_patient(patient_id: str, payload: dict = Depends(verify_token)):
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    if patient["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Delete patient
    await db.patients.delete_one({"id": patient_id})
    
    # Delete all related records
    await db.schede_impianto_picc.delete_many({"patient_id": patient_id})
    await db.schede_gestione_picc.delete_many({"patient_id": patient_id})
    await db.schede_medicazione_med.delete_many({"patient_id": patient_id})
    await db.appointments.delete_many({"patient_id": patient_id})
    await db.prescrizioni.delete_many({"patient_id": patient_id})
    await db.photos.delete_many({"patient_id": patient_id})
    
    return {"message": "Paziente e tutte le schede correlate eliminati"}

# ============== BATCH PATIENT OPERATIONS ==============
class BatchPatientCreate(BaseModel):
    patients: List[PatientCreate]

class BatchStatusChange(BaseModel):
    patient_ids: List[str]
    status: PatientStatus
    discharge_reason: Optional[str] = None
    discharge_notes: Optional[str] = None
    suspend_notes: Optional[str] = None

class BatchDelete(BaseModel):
    patient_ids: List[str]

@api_router.post("/patients/batch", status_code=201)
async def create_patients_batch(data: BatchPatientCreate, payload: dict = Depends(verify_token)):
    """Create multiple patients at once"""
    created = []
    errors = []
    impianti_created = 0
    
    for patient_data in data.patients:
        try:
            if patient_data.ambulatorio.value not in payload["ambulatori"]:
                errors.append({"patient": f"{patient_data.cognome} {patient_data.nome}", "error": "Non hai accesso a questo ambulatorio"})
                continue
            
            # Villa Ginestre only allows PICC
            if patient_data.ambulatorio == Ambulatorio.VILLA_GINESTRE and patient_data.tipo != PatientType.PICC:
                errors.append({"patient": f"{patient_data.cognome} {patient_data.nome}", "error": "Villa delle Ginestre gestisce solo pazienti PICC"})
                continue
            
            # Generate unique patient code
            codice_paziente = generate_patient_code(patient_data.nome, patient_data.cognome)
            while await db.patients.find_one({"codice_paziente": codice_paziente}):
                codice_paziente = generate_patient_code(patient_data.nome, patient_data.cognome)
            
            # Estrai i dati dell'impianto prima di creare il paziente
            tipo_impianto = patient_data.tipo_impianto
            data_inserimento_impianto = patient_data.data_inserimento_impianto
            
            patient_dict = patient_data.model_dump()
            # Rimuovi i campi impianto dal paziente (sono per la scheda impianto)
            patient_dict.pop("tipo_impianto", None)
            patient_dict.pop("data_inserimento_impianto", None)
            patient_dict["codice_paziente"] = codice_paziente
            patient_dict["scheda_med_counter"] = 0
            patient = Patient(**patient_dict)
            doc = patient.model_dump()
            await db.patients.insert_one(doc)
            created.append(patient)
            
            # Se è un paziente PICC e ha dati dell'impianto, crea la scheda impianto
            if (patient_data.tipo in [PatientType.PICC, PatientType.PICC_MED] 
                and tipo_impianto and data_inserimento_impianto):
                scheda_impianto = {
                    "id": str(uuid.uuid4()),
                    "patient_id": patient.id,
                    "ambulatorio": patient_data.ambulatorio.value,
                    "scheda_type": "semplificata",
                    "tipo_catetere": tipo_impianto,  # picc, picc_port, midline
                    "data_posizionamento": data_inserimento_impianto,
                    "data_impianto": data_inserimento_impianto,
                    "braccio": "",
                    "vena": "",
                    "exit_site_cm": "",
                    "operatore": payload.get("sub", ""),  # Nome dell'operatore corrente
                    "motivazione": [],
                    "disinfezione": [],
                    "allegati": [],
                    "created_at": datetime.now(timezone.utc).isoformat()
                }
                await db.schede_impianto_picc.insert_one(scheda_impianto)
                impianti_created += 1
                
        except Exception as e:
            errors.append({"patient": f"{patient_data.cognome} {patient_data.nome}", "error": str(e)})
    
    return {
        "created": len(created),
        "errors": len(errors),
        "impianti_created": impianti_created,
        "patients": [p.model_dump() for p in created],
        "error_details": errors
    }

@api_router.put("/patients/batch/status")
async def update_patients_status_batch(data: BatchStatusChange, payload: dict = Depends(verify_token)):
    """Change status of multiple patients at once"""
    updated = []
    errors = []
    
    for patient_id in data.patient_ids:
        try:
            patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
            if not patient:
                errors.append({"patient_id": patient_id, "error": "Paziente non trovato"})
                continue
            
            if patient["ambulatorio"] not in payload["ambulatori"]:
                errors.append({"patient_id": patient_id, "error": "Non hai accesso a questo ambulatorio"})
                continue
            
            update_data = {
                "status": data.status.value,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            
            if data.status == PatientStatus.DIMESSO:
                update_data["discharge_reason"] = data.discharge_reason
                update_data["discharge_notes"] = data.discharge_notes
                update_data["data_dimissione"] = datetime.now().strftime("%Y-%m-%d")
            elif data.status == PatientStatus.SOSPESO:
                update_data["suspend_notes"] = data.suspend_notes
            
            await db.patients.update_one({"id": patient_id}, {"$set": update_data})
            updated.append({"id": patient_id, "nome": f"{patient['cognome']} {patient['nome']}"})
        except Exception as e:
            errors.append({"patient_id": patient_id, "error": str(e)})
    
    return {
        "updated": len(updated),
        "errors": len(errors),
        "patients": updated,
        "error_details": errors
    }

@api_router.post("/patients/batch/delete")
async def delete_patients_batch(data: BatchDelete, payload: dict = Depends(verify_token)):
    """Delete multiple patients at once"""
    deleted = []
    errors = []
    
    for patient_id in data.patient_ids:
        try:
            patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
            if not patient:
                errors.append({"patient_id": patient_id, "error": "Paziente non trovato"})
                continue
            
            if patient["ambulatorio"] not in payload["ambulatori"]:
                errors.append({"patient_id": patient_id, "error": "Non hai accesso a questo ambulatorio"})
                continue
            
            # Delete patient and all related records
            await db.patients.delete_one({"id": patient_id})
            await db.schede_impianto_picc.delete_many({"patient_id": patient_id})
            await db.schede_gestione_picc.delete_many({"patient_id": patient_id})
            await db.schede_medicazione_med.delete_many({"patient_id": patient_id})
            await db.appointments.delete_many({"patient_id": patient_id})
            await db.prescrizioni.delete_many({"patient_id": patient_id})
            await db.photos.delete_many({"patient_id": patient_id})
            
            deleted.append({"id": patient_id, "nome": f"{patient['cognome']} {patient['nome']}"})
        except Exception as e:
            errors.append({"patient_id": patient_id, "error": str(e)})
    
    return {
        "deleted": len(deleted),
        "errors": len(errors),
        "patients": deleted,
        "error_details": errors
    }

# ============== BATCH IMPLANTS ROUTES ==============
class BatchImplantCreate(BaseModel):
    implants: list[dict]  # Lista di {patient_id, tipo_impianto, data_inserimento}

@api_router.post("/implants/batch", status_code=201)
async def create_implants_batch(data: BatchImplantCreate, payload: dict = Depends(verify_token)):
    """Create multiple implants for existing PICC patients"""
    created = []
    errors = []
    
    for implant_data in data.implants:
        try:
            patient_id = implant_data.get("patient_id")
            tipo_impianto = implant_data.get("tipo_impianto")
            data_inserimento = implant_data.get("data_inserimento")
            
            if not patient_id or not tipo_impianto or not data_inserimento:
                errors.append({"patient_id": patient_id, "error": "Dati incompleti (richiesto: patient_id, tipo_impianto, data_inserimento)"})
                continue
            
            # Verifica paziente esiste ed è PICC
            patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
            if not patient:
                errors.append({"patient_id": patient_id, "error": "Paziente non trovato"})
                continue
            
            if patient["ambulatorio"] not in payload["ambulatori"]:
                errors.append({"patient_id": patient_id, "error": "Non hai accesso a questo ambulatorio"})
                continue
            
            if patient.get("tipo") not in ["PICC", "PICC_MED"]:
                errors.append({"patient_id": patient_id, "error": f"Il paziente {patient.get('cognome')} {patient.get('nome')} non è di tipo PICC"})
                continue
            
            # Crea scheda impianto
            scheda_impianto = {
                "id": str(uuid.uuid4()),
                "patient_id": patient_id,
                "ambulatorio": patient["ambulatorio"],
                "scheda_type": "semplificata",
                "tipo_catetere": tipo_impianto,
                "data_posizionamento": data_inserimento,
                "data_impianto": data_inserimento,
                "braccio": "",
                "vena": "",
                "exit_site_cm": "",
                "operatore": payload.get("sub", ""),
                "motivazione": [],
                "disinfezione": [],
                "allegati": [],
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await db.schede_impianto_picc.insert_one(scheda_impianto)
            created.append({
                "id": scheda_impianto["id"],
                "patient_id": patient_id,
                "patient_name": f"{patient.get('cognome')} {patient.get('nome')}",
                "tipo_impianto": tipo_impianto,
                "data_inserimento": data_inserimento
            })
            
        except Exception as e:
            errors.append({"patient_id": implant_data.get("patient_id"), "error": str(e)})
    
    return {
        "created": len(created),
        "errors": len(errors),
        "implants": created,
        "error_details": errors
    }

@api_router.get("/patients/picc/search")
async def search_picc_patients(q: str = "", ambulatorio: str = "", payload: dict = Depends(verify_token)):
    """Search PICC patients for implant batch creation"""
    if ambulatorio and ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {
        "tipo": {"$in": ["PICC", "PICC_MED"]},
        "status": "in_cura"
    }
    
    if ambulatorio:
        query["ambulatorio"] = ambulatorio
    else:
        query["ambulatorio"] = {"$in": payload["ambulatori"]}
    
    if q:
        query["$or"] = [
            {"nome": {"$regex": q, "$options": "i"}},
            {"cognome": {"$regex": q, "$options": "i"}}
        ]
    
    patients = await db.patients.find(query, {"_id": 0, "id": 1, "nome": 1, "cognome": 1, "tipo": 1}).to_list(50)
    return patients

# ============== APPOINTMENTS ROUTES ==============
@api_router.post("/appointments", response_model=Appointment)
async def create_appointment(data: AppointmentCreate, payload: dict = Depends(verify_token)):
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Get patient info
    patient = await db.patients.find_one({"id": data.patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    
    # Check slot availability (max 2 per type per slot)
    existing = await db.appointments.count_documents({
        "ambulatorio": data.ambulatorio.value,
        "data": data.data,
        "ora": data.ora,
        "tipo": data.tipo
    })
    if existing >= 2:
        raise HTTPException(status_code=400, detail="Slot pieno (max 2 pazienti)")
    
    appointment = Appointment(
        **data.model_dump(),
        patient_nome=patient["nome"],
        patient_cognome=patient["cognome"]
    )
    doc = appointment.model_dump()
    await db.appointments.insert_one(doc)
    return appointment

@api_router.get("/appointments", response_model=List[Appointment])
async def get_appointments(
    ambulatorio: Ambulatorio,
    data: Optional[str] = None,
    data_from: Optional[str] = None,
    data_to: Optional[str] = None,
    tipo: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {"ambulatorio": ambulatorio.value}
    if data:
        query["data"] = data
    elif data_from and data_to:
        query["data"] = {"$gte": data_from, "$lte": data_to}
    if tipo:
        query["tipo"] = tipo
    
    appointments = await db.appointments.find(query, {"_id": 0}).sort([("data", 1), ("ora", 1)]).to_list(1000)
    
    # Ensure patient names are populated (for old appointments without this data)
    for apt in appointments:
        if not apt.get("patient_nome") or not apt.get("patient_cognome"):
            patient = await db.patients.find_one({"id": apt.get("patient_id")}, {"_id": 0})
            if patient:
                apt["patient_nome"] = patient.get("nome", "")
                apt["patient_cognome"] = patient.get("cognome", "")
                # Update the appointment in DB for future queries
                await db.appointments.update_one(
                    {"id": apt["id"]},
                    {"$set": {"patient_nome": apt["patient_nome"], "patient_cognome": apt["patient_cognome"]}}
                )
    
    return appointments

@api_router.put("/appointments/{appointment_id}", response_model=Appointment)
async def update_appointment(appointment_id: str, data: dict, payload: dict = Depends(verify_token)):
    appointment = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not appointment:
        raise HTTPException(status_code=404, detail="Appuntamento non trovato")
    if appointment["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.appointments.update_one({"id": appointment_id}, {"$set": data})
    updated = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    return updated

@api_router.delete("/appointments/{appointment_id}")
async def delete_appointment(appointment_id: str, payload: dict = Depends(verify_token)):
    appointment = await db.appointments.find_one({"id": appointment_id}, {"_id": 0})
    if not appointment:
        raise HTTPException(status_code=404, detail="Appuntamento non trovato")
    if appointment["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.appointments.delete_one({"id": appointment_id})
    return {"message": "Appuntamento eliminato"}

# ============== SLOT CHIUSI (CHIUDI AGENDA) ==============
class ClosedSlotCreate(BaseModel):
    data: str  # YYYY-MM-DD
    ambulatorio: Ambulatorio
    ora: Optional[Union[str, list]] = None  # Singolo orario, lista di orari, o None per tutta la giornata
    tipo: Optional[str] = None  # PICC o MED, se None chiude entrambi
    motivo: Optional[str] = None

@api_router.post("/closed-slots")
async def create_closed_slots(data: ClosedSlotCreate, payload: dict = Depends(verify_token)):
    """Chiude uno o più slot"""
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    created_slots = []
    
    # Se ci sono più orari, crea uno slot per ogni orario
    orari = data.ora if isinstance(data.ora, list) else ([data.ora] if data.ora else [None])
    
    for ora in orari:
        closed_slot = {
            "id": str(uuid.uuid4()),
            "data": data.data,
            "ambulatorio": data.ambulatorio.value,
            "ora": ora,  # None = tutta la giornata
            "tipo": data.tipo,  # None = entrambi PICC e MED
            "motivo": data.motivo or "Chiuso",
            "created_by": payload.get("sub", ""),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Evita duplicati
        existing = await db.closed_slots.find_one({
            "data": data.data,
            "ambulatorio": data.ambulatorio.value,
            "ora": ora,
            "tipo": data.tipo
        })
        
        if not existing:
            await db.closed_slots.insert_one(closed_slot)
            # Restituisci senza _id
            created_slots.append({k: v for k, v in closed_slot.items() if k != "_id"})
    
    return {"created": len(created_slots), "slots": created_slots}

@api_router.get("/closed-slots")
async def get_closed_slots(
    ambulatorio: str,
    data: Optional[str] = None,
    data_from: Optional[str] = None,
    data_to: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    """Ottiene gli slot chiusi per un ambulatorio"""
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {"ambulatorio": ambulatorio}
    if data:
        query["data"] = data
    elif data_from and data_to:
        query["data"] = {"$gte": data_from, "$lte": data_to}
    
    closed_slots = await db.closed_slots.find(query, {"_id": 0}).to_list(1000)
    return closed_slots

@api_router.delete("/closed-slots/{slot_id}")
async def delete_closed_slot(slot_id: str, payload: dict = Depends(verify_token)):
    """Riapre uno slot chiuso"""
    closed_slot = await db.closed_slots.find_one({"id": slot_id}, {"_id": 0})
    if not closed_slot:
        raise HTTPException(status_code=404, detail="Slot chiuso non trovato")
    if closed_slot["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.closed_slots.delete_one({"id": slot_id})
    return {"message": "Slot riaperto"}

@api_router.post("/closed-slots/reopen-day")
async def reopen_day(data: dict, payload: dict = Depends(verify_token)):
    """Riapre tutti gli slot chiusi di una giornata"""
    ambulatorio = data.get("ambulatorio")
    data_str = data.get("data")
    
    if not ambulatorio or not data_str:
        raise HTTPException(status_code=400, detail="ambulatorio e data sono richiesti")
    
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    result = await db.closed_slots.delete_many({"ambulatorio": ambulatorio, "data": data_str})
    return {"message": f"{result.deleted_count} slot riaperti", "deleted_count": result.deleted_count}

# ============== SCHEDE MEDICAZIONE MED ==============
@api_router.post("/schede-medicazione-med", response_model=SchedaMedicazioneMED)
async def create_scheda_medicazione_med(data: SchedaMedicazioneMEDCreate, payload: dict = Depends(verify_token)):
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Get patient to use their code
    patient = await db.patients.find_one({"id": data.patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    
    # Get or generate patient code
    codice_paziente = patient.get("codice_paziente")
    if not codice_paziente:
        # Generate code for existing patient without one
        codice_paziente = generate_patient_code(patient.get("nome", ""), patient.get("cognome", ""))
        while await db.patients.find_one({"codice_paziente": codice_paziente, "id": {"$ne": data.patient_id}}):
            codice_paziente = generate_patient_code(patient.get("nome", ""), patient.get("cognome", ""))
        await db.patients.update_one({"id": data.patient_id}, {"$set": {"codice_paziente": codice_paziente}})
    
    # Get next scheda number for this patient
    counter = patient.get("scheda_med_counter", 0) + 1
    await db.patients.update_one({"id": data.patient_id}, {"$set": {"scheda_med_counter": counter}})
    
    # Generate scheda code: codice_paziente-numero (es. m234h-1)
    codice = f"{codice_paziente}-{counter}"
    
    scheda_data = data.model_dump()
    scheda_data["codice"] = codice
    scheda = SchedaMedicazioneMED(**scheda_data)
    doc = scheda.model_dump()
    await db.schede_medicazione_med.insert_one(doc)
    return scheda

@api_router.get("/schede-medicazione-med", response_model=List[SchedaMedicazioneMED])
async def get_schede_medicazione_med(
    patient_id: str,
    ambulatorio: Ambulatorio,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    schede = await db.schede_medicazione_med.find(
        {"patient_id": patient_id, "ambulatorio": ambulatorio.value},
        {"_id": 0}
    ).sort("data_compilazione", -1).to_list(1000)
    return schede

@api_router.get("/schede-medicazione-med/{scheda_id}", response_model=SchedaMedicazioneMED)
async def get_scheda_medicazione_med(scheda_id: str, payload: dict = Depends(verify_token)):
    scheda = await db.schede_medicazione_med.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    return scheda

@api_router.put("/schede-medicazione-med/{scheda_id}", response_model=SchedaMedicazioneMED)
async def update_scheda_medicazione_med(scheda_id: str, data: dict, payload: dict = Depends(verify_token)):
    scheda = await db.schede_medicazione_med.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.schede_medicazione_med.update_one({"id": scheda_id}, {"$set": data})
    updated = await db.schede_medicazione_med.find_one({"id": scheda_id}, {"_id": 0})
    return updated

# ============== SCHEDE IMPIANTO PICC ==============
@api_router.post("/schede-impianto-picc", response_model=SchedaImpiantoPICC)
async def create_scheda_impianto_picc(data: SchedaImpiantoPICCCreate, payload: dict = Depends(verify_token)):
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    scheda = SchedaImpiantoPICC(**data.model_dump())
    doc = scheda.model_dump()
    await db.schede_impianto_picc.insert_one(doc)
    return scheda

@api_router.get("/schede-impianto-picc", response_model=List[SchedaImpiantoPICC])
async def get_schede_impianto_picc(
    patient_id: str,
    ambulatorio: Ambulatorio,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    schede = await db.schede_impianto_picc.find(
        {"patient_id": patient_id, "ambulatorio": ambulatorio.value},
        {"_id": 0}
    ).sort("data_impianto", -1).to_list(1000)
    return schede

@api_router.put("/schede-impianto-picc/{scheda_id}", response_model=SchedaImpiantoPICC)
async def update_scheda_impianto_picc(scheda_id: str, data: dict, payload: dict = Depends(verify_token)):
    scheda = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.schede_impianto_picc.update_one({"id": scheda_id}, {"$set": data})
    updated = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    return updated

# ============== IMPIANTI LIST ENDPOINT ==============
@api_router.get("/impianti")
async def get_impianti_list(
    ambulatorio: str,
    anno: int = None,
    mese: int = None,
    tipo_impianto: str = None,
    payload: dict = Depends(verify_token)
):
    """Get all implants with patient info, filterable by date and type"""
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Build query for schede_impianto_picc
    query = {"ambulatorio": ambulatorio}
    
    # Filter by tipo_catetere if specified
    if tipo_impianto and tipo_impianto != "tutti":
        query["tipo_catetere"] = tipo_impianto
    
    # Get all schede that match the query
    schede = await db.schede_impianto_picc.find(query, {"_id": 0}).to_list(10000)
    
    # Build result with patient info
    result = []
    for scheda in schede:
        # Get the implant date
        data_impianto = scheda.get("data_posizionamento") or scheda.get("data_impianto")
        if not data_impianto:
            continue
            
        # Parse date for filtering
        try:
            # Handle different date formats
            if "/" in data_impianto:
                parts = data_impianto.split("/")
                if len(parts) == 3:
                    if len(parts[2]) == 2:
                        parts[2] = "20" + parts[2]
                    parsed_date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
            else:
                parsed_date = data_impianto
                
            date_obj = datetime.strptime(parsed_date, "%Y-%m-%d")
            
            # Filter by anno
            if anno and date_obj.year != anno:
                continue
            # Filter by mese
            if mese and date_obj.month != mese:
                continue
                
        except (ValueError, IndexError):
            # Skip entries with invalid dates if filtering is active
            if anno or mese:
                continue
        
        # Get patient info
        patient = await db.patients.find_one({"id": scheda["patient_id"]}, {"_id": 0})
        if not patient:
            continue
        
        result.append({
            "scheda_id": scheda["id"],
            "patient_id": scheda["patient_id"],
            "patient_nome": patient.get("nome", ""),
            "patient_cognome": patient.get("cognome", ""),
            "data_impianto": data_impianto,
            "data_impianto_parsed": parsed_date if 'parsed_date' in dir() else data_impianto,
            "tipo_impianto": scheda.get("tipo_catetere", "N/D"),
            "ambulatorio": ambulatorio
        })
    
    # Sort by date (most recent first within each month)
    def parse_date_for_sort(item):
        try:
            d = item.get("data_impianto_parsed") or item.get("data_impianto")
            if "/" in d:
                parts = d.split("/")
                if len(parts) == 3:
                    if len(parts[2]) == 2:
                        parts[2] = "20" + parts[2]
                    return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
            return d
        except:
            return "0000-00-00"
    
    result.sort(key=lambda x: parse_date_for_sort(x), reverse=False)
    
    return {
        "impianti": result,
        "count": len(result),
        "filters": {
            "anno": anno,
            "mese": mese,
            "tipo_impianto": tipo_impianto
        }
    }

# ============== ESPIANTI LIST ENDPOINT ==============
@api_router.get("/espianti")
async def get_espianti_list(
    ambulatorio: str,
    anno: int = None,
    mese: int = None,
    payload: dict = Depends(verify_token)
):
    """Get all espianti (from appointments with espianto prestazioni)"""
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Build date range
    if anno:
        if mese:
            start_date = f"{anno}-{mese:02d}-01"
            if mese == 12:
                end_date = f"{anno + 1}-01-01"
            else:
                end_date = f"{anno}-{mese + 1:02d}-01"
        else:
            start_date = f"{anno}-01-01"
            end_date = f"{anno + 1}-01-01"
    else:
        start_date = "2020-01-01"
        end_date = "2030-12-31"
    
    # Query appointments with espianto prestazioni
    espianto_types = ["espianto_picc", "espianto_picc_port", "espianto_midline"]
    
    query = {
        "ambulatorio": ambulatorio,
        "data": {"$gte": start_date, "$lt": end_date},
        "prestazioni": {"$in": espianto_types}
    }
    
    appointments = await db.appointments.find(query, {"_id": 0}).to_list(10000)
    
    # Build result
    result = []
    for apt in appointments:
        prestazioni = apt.get("prestazioni", [])
        
        # Find which espianto type
        espianto_tipo = None
        for et in espianto_types:
            if et in prestazioni:
                espianto_tipo = et
                break
        
        if not espianto_tipo:
            continue
        
        # Get patient info
        patient = await db.patients.find_one({"id": apt.get("patient_id")}, {"_id": 0})
        
        result.append({
            "appointment_id": apt.get("id"),
            "patient_id": apt.get("patient_id"),
            "patient_nome": patient.get("nome", "") if patient else apt.get("patient_nome", ""),
            "patient_cognome": patient.get("cognome", "") if patient else apt.get("patient_cognome", ""),
            "data_espianto": apt.get("data"),
            "tipo_espianto": espianto_tipo,
            "ambulatorio": ambulatorio
        })
    
    # Sort by date
    result.sort(key=lambda x: x.get("data_espianto", ""), reverse=False)
    
    return {
        "espianti": result,
        "count": len(result),
        "filters": {
            "anno": anno,
            "mese": mese
        }
    }

# Generate PDF for Scheda Impianto PICC in official format
@api_router.get("/schede-impianto-picc/{scheda_id}/pdf")
async def download_scheda_impianto_pdf(scheda_id: str, payload: dict = Depends(verify_token)):
    """Download scheda impianto PICC as PDF in official format"""
    scheda = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Get patient info
    patient = await db.patients.find_one({"id": scheda["patient_id"]}, {"_id": 0})
    
    # Generate PDF
    pdf_bytes = generate_scheda_impianto_pdf(scheda, patient)
    
    # Use data_posizionamento or data_impianto for filename
    data_file = scheda.get('data_posizionamento') or scheda.get('data_impianto') or 'nd'
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=scheda_impianto_{data_file}.pdf"}
    )

def generate_scheda_impianto_pdf(scheda: dict, patient: dict) -> bytes:
    """Generate PDF for Scheda Impianto PICC - EXACT format as per official form"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=0.5*cm, bottomMargin=0.5*cm, leftMargin=0.8*cm, rightMargin=0.8*cm)
    story = []
    
    # Styles
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', fontSize=11, alignment=1, fontName='Helvetica-Bold', spaceAfter=3)
    section_header = ParagraphStyle('SectionHeader', fontSize=9, fontName='Helvetica-Bold', alignment=1, 
                                    backColor=colors.HexColor('#e5e7eb'), spaceBefore=6, spaceAfter=3)
    normal_style = ParagraphStyle('Normal', fontSize=7, spaceAfter=2, fontName='Helvetica')
    small_style = ParagraphStyle('Small', fontSize=6.5, spaceAfter=1, fontName='Helvetica')
    italic_small = ParagraphStyle('ItalicSmall', fontSize=6, fontName='Helvetica-Oblique', textColor=colors.grey)
    
    def cb(checked):
        """Checkbox helper - simple text representation"""
        if checked:
            return "[X]"  # Checked
        else:
            return "[  ]"  # Empty
    
    def cb_list(arr, val):
        """Check if value is in list"""
        is_checked = isinstance(arr, list) and val in arr
        return cb(is_checked)
    
    def get_val(key, default=""):
        """Get value from scheda, return default only if None"""
        val = scheda.get(key)
        if val is None:
            return default
        return val
    
    # === HEADER ===
    story.append(Paragraph("SCHEDA IMPIANTO e GESTIONE ACCESSI VENOSI", title_style))
    story.append(Paragraph("Allegato n. 2", ParagraphStyle('Right', fontSize=7, alignment=2)))
    story.append(Spacer(1, 5))
    
    # Patient Info Box - Header info
    patient_name = f"{patient.get('cognome', '')} {patient.get('nome', '')}" if patient else ""
    patient_dob = patient.get('data_nascita', '') if patient else ""
    patient_sex = patient.get('sesso', '') if patient else ""
    
    # Format sesso properly
    sesso_display = f"{cb(patient_sex == 'M')} M   {cb(patient_sex == 'F')} F"
    
    header_data = [
        [Paragraph("<b>Presidio Ospedaliero/Struttura Sanitaria:</b>", small_style), 
         get_val('presidio_ospedaliero'), 
         Paragraph("<b>Codice:</b>", small_style), 
         get_val('codice'),
         Paragraph("<b>U.O.:</b>", small_style), 
         get_val('unita_operativa')],
        [Paragraph("<b>Cognome e Nome Paziente:</b>", small_style), 
         patient_name,
         Paragraph("<b>Data di nascita:</b>", small_style), 
         patient_dob,
         Paragraph("<b>Sesso:</b>", small_style), 
         sesso_display],
        [Paragraph("<b>Preso in carico dalla struttura dal:</b>", small_style), 
         get_val('data_presa_carico'),
         Paragraph("<b>Cartella Clinica n.:</b>", small_style), 
         get_val('cartella_clinica'), "", ""],
    ]
    t = Table(header_data, colWidths=[4.5*cm, 4*cm, 2.5*cm, 2.5*cm, 1.5*cm, 3.5*cm])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 6.5),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))
    
    # === SECTION 1: CATETERE GIÀ PRESENTE ===
    story.append(Paragraph("SEZIONE CATETERE GIÀ PRESENTE", section_header))
    story.append(Paragraph("(Da compilare se catetere già presente al momento della presa in carico ambulatoriale o in regime di degenza)", italic_small))
    story.append(Spacer(1, 3))
    
    tipo_presente = get_val('catetere_presente_tipo')
    tipo_opts = [
        ("cvc_non_tunnellizzato", "CVC non tunnellizzato (breve termine)"),
        ("cvc_tunnellizzato", "CVC tunnellizzato (lungo termine tipo Groshong, Hickman, Broviac)"),
        ("picc", "CVC medio termine (PICC)"),
        ("port", "PORT (lungo termine)"),
        ("midline", "Midline"),
    ]
    
    tipo_line = "Tipo di Catetere: " + "  ".join([f"{cb(tipo_presente == opt[0])} {opt[1]}" for opt in tipo_opts])
    story.append(Paragraph(tipo_line, small_style))
    story.append(Spacer(1, 2))
    
    story.append(Paragraph(f"Riportare: - Struttura/reparto dove il catetere è stato inserito: {get_val('catetere_presente_struttura')}", small_style))
    
    mod_presente = get_val('catetere_presente_modalita')
    story.append(Paragraph(f"data: {get_val('catetere_presente_data')}  ora: {get_val('catetere_presente_ora')}  modalità: {cb(mod_presente == 'emergenza_urgenza')} emergenza/urgenza  {cb(mod_presente == 'programmato_elezione')} programmato/elezione", small_style))
    
    rx_presente = get_val('catetere_presente_rx')
    story.append(Paragraph(f"Se è stato effettuato controllo RX Post-Inserimento: {cb(rx_presente == True)} SI  {cb(rx_presente == False)} NO", small_style))
    
    da_sostituire = get_val('catetere_da_sostituire')
    story.append(Paragraph(f"Catetere da sostituire: {cb(da_sostituire == True)} SI  {cb(da_sostituire == False)} NO   se si compilare la SEZIONE IMPIANTO", small_style))
    
    story.append(Spacer(1, 8))
    
    # === SECTION 2: IMPIANTO CATETERE ===
    story.append(Paragraph("SEZIONE IMPIANTO CATETERE", section_header))
    story.append(Paragraph("(Da compilare se catetere viene impiantato nella struttura)", italic_small))
    story.append(Spacer(1, 3))
    
    # TIPO DI CATETERE - Aggiornato con nuove opzioni
    tipo = get_val('tipo_catetere')
    tipo_opts_new = [
        ("picc", "PICC"),
        ("midline", "Midline"),
        ("picc_port", "PICC Port"),
        ("port_a_cath", "PORT a cath"),
        ("altro", "Altro"),
    ]
    tipo_line = "<b>TIPO DI CATETERE:</b> " + "  ".join([f"{cb(tipo == opt[0])} {opt[1]}" for opt in tipo_opts_new])
    if tipo == 'altro':
        tipo_line += f" specificare: {get_val('tipo_catetere_altro')}"
    story.append(Paragraph(tipo_line, small_style))
    story.append(Spacer(1, 2))
    
    # POSIZIONAMENTO: Solo Braccio e Vena (semplificato come da frontend)
    braccio = get_val('braccio')
    vena = get_val('vena')
    vena_altro = get_val('vena_altro')
    pos_line = f"<b>BRACCIO:</b> {cb(braccio == 'dx')} dx  {cb(braccio == 'sn')} sn    <b>VENA:</b> {cb(vena == 'basilica')} Basilica  {cb(vena == 'cefalica')} Cefalica  {cb(vena == 'brachiale')} Brachiale  {cb(vena == 'altro')} Altro"
    if vena == 'altro' and vena_altro:
        pos_line += f" ({vena_altro})"
    story.append(Paragraph(pos_line, small_style))
    
    # Nuovi campi misure catetere
    misure_line = f"<b>Diametro vena:</b> {get_val('diametro_vena_mm')} mm    <b>Profondità:</b> {get_val('profondita_cm')} cm    <b>Exit-site:</b> {get_val('exit_site_cm')} cm"
    story.append(Paragraph(misure_line, small_style))
    
    # Misure catetere SENZA LOTTO
    catetere_line = f"<b>Lunghezza totale:</b> {get_val('lunghezza_totale_cm')} cm    <b>Lunghezza impiantata:</b> {get_val('lunghezza_impiantata_cm')} cm    <b>French:</b> {get_val('french')}    <b>Lumi:</b> {get_val('lumi')}"
    story.append(Paragraph(catetere_line, small_style))
    story.append(Spacer(1, 3))
    
    # PROCEDURE DETAILS
    val_sito = get_val('valutazione_sito')
    story.append(Paragraph(f"<b>VALUTAZIONE MIGLIOR SITO DI INSERIMENTO:</b>  {cb(val_sito == True)} SI  {cb(val_sito == False)} NO", small_style))
    
    eco = get_val('ecoguidato')
    story.append(Paragraph(f"<b>IMPIANTO ECOGUIDATO:</b>  {cb(eco == True)} SI  {cb(eco == False)} NO", small_style))
    
    igiene = get_val('igiene_mani')
    story.append(Paragraph(f"<b>IGIENE DELLE MANI (LAVAGGIO ANTISETTICO DELLE MANI O FRIZIONE ALCOLICA):</b>  {cb(igiene == True)} SI  {cb(igiene == False)} NO", small_style))
    
    prec = get_val('precauzioni_barriera')
    story.append(Paragraph(f"<b>UTILIZZO MASSIME PRECAUZIONI DI BARRIERA</b> (berretto, maschera, camice sterile, guanti sterili, telo sterile sul paziente): {cb(prec == True)} SI  {cb(prec == False)} NO", small_style))
    
    # DISINFEZIONE
    disinfezione = get_val('disinfezione') or []
    story.append(Paragraph(f"<b>DISINFEZIONE DELLA CUTE INTEGRA:</b>  {cb_list(disinfezione, 'clorexidina_2')} CLOREXIDINA IN SOLUZIONE ALCOLICA 2%    {cb_list(disinfezione, 'iodiopovidone')} IODIOPOVIDONE", small_style))
    
    # COLLA HYSTOACRILICA (nuovo)
    colla = get_val('colla_hystoacrilica')
    story.append(Paragraph(f"<b>UTILIZZO COLLA HYSTOACRILICA:</b>  {cb(colla == True)} SI  {cb(colla == False)} NO", small_style))
    
    # DISPOSITIVI
    sut = get_val('sutureless_device')
    story.append(Paragraph(f"<b>IMPIEGO DI \"SUTURELESS DEVICES\" PER IL FISSAGGIO DEL CATETERE:</b>  {cb(sut == True)} SI  {cb(sut == False)} NO", small_style))
    
    med_trasp = get_val('medicazione_trasparente')
    med_occl = get_val('medicazione_occlusiva')
    story.append(Paragraph(f"<b>IMPIEGO DI MEDICAZIONE SEMIPERMEABILE TRASPARENTE:</b>  {cb(med_trasp == True)} SI  {cb(med_trasp == False)} NO    <b>IMPIEGO DI MEDICAZIONE OCCLUSIVA:</b>  {cb(med_occl == True)} SI  {cb(med_occl == False)} NO", small_style))
    
    # CONTROLLI (aggiornato con ECG intracavitario)
    rx_post = get_val('controllo_rx')
    ecg_intra = get_val('ecg_intracavitario')
    story.append(Paragraph(f"<b>CONTROLLO RX POST-INSERIMENTO:</b>  {cb(rx_post == True)} SI  {cb(rx_post == False)} NO    <b>ECG INTRACAVITARIO:</b>  {cb(ecg_intra == True)} SI  {cb(ecg_intra == False)} NO", small_style))
    
    # MODALITÀ
    mod = get_val('modalita')
    story.append(Paragraph(f"<b>MODALITÀ:</b>  {cb(mod == 'emergenza')} EMERGENZA  {cb(mod == 'urgenza')} URGENZA  {cb(mod == 'elezione')} ELEZIONE", small_style))
    
    # MOTIVAZIONE - Aggiornato con nuove opzioni
    motivazione = get_val('motivazione') or []
    motiv_line = f"<b>MOTIVAZIONE DI INSERIMENTO CVC:</b>  {cb_list(motivazione, 'chemioterapia')} chemioterapia  {cb_list(motivazione, 'scarso_patrimonio_venoso')} scarso patrimonio venoso  {cb_list(motivazione, 'npt')} NPT  {cb_list(motivazione, 'monitoraggio')} monitoraggio invasivo  {cb_list(motivazione, 'altro')} altro"
    if 'altro' in motivazione:
        motiv_line += f" (specificare): {get_val('motivazione_altro')}"
    story.append(Paragraph(motiv_line, small_style))
    
    story.append(Spacer(1, 10))
    
    # === FOOTER con 1° e 2° operatore ===
    data_pos = get_val('data_posizionamento') or get_val('data_impianto')
    footer_data = [
        [Paragraph("<b>DATA POSIZIONAMENTO:</b>", small_style), data_pos, "", ""],
        [Paragraph("<b>1° OPERATORE:</b>", small_style), get_val('operatore'), Paragraph("<b>FIRMA:</b>", small_style), "________________"],
        [Paragraph("<b>2° OPERATORE:</b>", small_style), get_val('secondo_operatore'), Paragraph("<b>FIRMA:</b>", small_style), "________________"],
    ]
    ft = Table(footer_data, colWidths=[4*cm, 6*cm, 2*cm, 6.5*cm])
    ft.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(ft)
    
    # Note se presenti
    if get_val('note'):
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"<b>NOTE:</b> {get_val('note')}", normal_style))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# ============== SCHEDE GESTIONE PICC (MENSILE) ==============
@api_router.post("/schede-gestione-picc", response_model=SchedaGestionePICC)
async def create_scheda_gestione_picc(data: SchedaGestionePICCCreate, payload: dict = Depends(verify_token)):
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Check if already exists for this month
    existing = await db.schede_gestione_picc.find_one({
        "patient_id": data.patient_id,
        "ambulatorio": data.ambulatorio.value,
        "mese": data.mese
    })
    if existing:
        raise HTTPException(status_code=400, detail="Esiste già una scheda per questo mese")
    
    scheda = SchedaGestionePICC(**data.model_dump())
    doc = scheda.model_dump()
    await db.schede_gestione_picc.insert_one(doc)
    return scheda

@api_router.get("/schede-gestione-picc", response_model=List[SchedaGestionePICC])
async def get_schede_gestione_picc(
    patient_id: str,
    ambulatorio: Ambulatorio,
    mese: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {"patient_id": patient_id, "ambulatorio": ambulatorio.value}
    if mese:
        query["mese"] = mese
    
    schede = await db.schede_gestione_picc.find(query, {"_id": 0}).sort("mese", -1).to_list(100)
    return schede

@api_router.put("/schede-gestione-picc/{scheda_id}", response_model=SchedaGestionePICC)
async def update_scheda_gestione_picc(scheda_id: str, data: dict, payload: dict = Depends(verify_token)):
    scheda = await db.schede_gestione_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.schede_gestione_picc.update_one({"id": scheda_id}, {"$set": data})
    updated = await db.schede_gestione_picc.find_one({"id": scheda_id}, {"_id": 0})
    return updated

# ============== PHOTOS / ATTACHMENTS ==============
@api_router.post("/photos")
async def upload_photo(
    patient_id: str = Form(...),
    ambulatorio: str = Form(...),
    tipo: str = Form(...),
    data: str = Form(...),
    descrizione: Optional[str] = Form(None),
    file_type: Optional[str] = Form("image"),
    original_name: Optional[str] = Form(None),
    scheda_med_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    payload: dict = Depends(verify_token)
):
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    contents = await file.read()
    image_data = base64.b64encode(contents).decode('utf-8')
    
    # Determine file type from content type if not provided
    mime_type = file.content_type
    if not file_type or file_type == "image":
        if mime_type and 'pdf' in mime_type:
            file_type = 'pdf'
        elif mime_type and ('word' in mime_type or 'document' in mime_type):
            file_type = 'word'
        elif mime_type and ('excel' in mime_type or 'spreadsheet' in mime_type):
            file_type = 'excel'
        elif mime_type and mime_type.startswith('image/'):
            file_type = 'image'
    
    photo = Photo(
        patient_id=patient_id,
        ambulatorio=Ambulatorio(ambulatorio),
        tipo=tipo,
        descrizione=descrizione,
        data=data,
        image_data=image_data,
        file_type=file_type,
        original_name=original_name or file.filename,
        mime_type=mime_type,
        scheda_med_id=scheda_med_id if scheda_med_id != "pending" else None
    )
    doc = photo.model_dump()
    await db.photos.insert_one(doc)
    
    return {"id": photo.id, "message": "File caricato"}

@api_router.get("/photos")
async def get_photos(
    patient_id: str,
    ambulatorio: Ambulatorio,
    tipo: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    query = {"patient_id": patient_id, "ambulatorio": ambulatorio.value}
    if tipo:
        query["tipo"] = tipo
    
    photos = await db.photos.find(query, {"_id": 0}).sort("data", -1).to_list(100)
    return photos

@api_router.get("/photos/{photo_id}")
async def get_photo(photo_id: str, payload: dict = Depends(verify_token)):
    photo = await db.photos.find_one({"id": photo_id}, {"_id": 0})
    if not photo:
        raise HTTPException(status_code=404, detail="Foto non trovata")
    if photo["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    return photo

@api_router.delete("/photos/{photo_id}")
async def delete_photo(photo_id: str, payload: dict = Depends(verify_token)):
    photo = await db.photos.find_one({"id": photo_id}, {"_id": 0})
    if not photo:
        raise HTTPException(status_code=404, detail="Foto non trovata")
    if photo["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.photos.delete_one({"id": photo_id})
    return {"message": "Foto eliminata"}

# ============== DOCUMENTS ==============
@api_router.get("/documents")
async def get_documents(
    ambulatorio: Ambulatorio,
    categoria: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    docs = DOCUMENT_TEMPLATES.copy()
    
    # Villa Ginestre only sees PICC documents
    if ambulatorio == Ambulatorio.VILLA_GINESTRE:
        docs = [d for d in docs if d["categoria"] == "PICC"]
    
    if categoria:
        docs = [d for d in docs if d["categoria"] == categoria]
    
    return docs

# ============== STATISTICS ==============
@api_router.get("/statistics")
async def get_statistics(
    ambulatorio: Ambulatorio,
    anno: int,
    mese: Optional[int] = None,
    tipo: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Villa Ginestre only shows PICC stats
    if ambulatorio == Ambulatorio.VILLA_GINESTRE and tipo == "MED":
        raise HTTPException(status_code=400, detail="Villa delle Ginestre non ha statistiche MED")
    
    # Build date range
    if mese:
        start_date = f"{anno}-{mese:02d}-01"
        if mese == 12:
            end_date = f"{anno + 1}-01-01"
        else:
            end_date = f"{anno}-{mese + 1:02d}-01"
    else:
        start_date = f"{anno}-01-01"
        end_date = f"{anno + 1}-01-01"
    
    query = {
        "ambulatorio": ambulatorio.value,
        "data": {"$gte": start_date, "$lt": end_date}
    }
    if tipo:
        query["tipo"] = tipo
    elif ambulatorio == Ambulatorio.VILLA_GINESTRE:
        query["tipo"] = "PICC"
    
    appointments = await db.appointments.find(query, {"_id": 0}).to_list(10000)
    
    # IMPORTANTE: Escludere i pazienti "non_presentato" dalle statistiche
    # Le prestazioni dei pazienti segnati in rosso (non presentati) non vengono contate
    appointments_validi = [app for app in appointments if app.get("stato") != "non_presentato"]
    
    # Calculate statistics (solo appuntamenti effettuati o da_fare, escludendo non_presentato)
    total_accessi = len(appointments_validi)
    unique_patients = len(set(a["patient_id"] for a in appointments_validi))
    
    # Prestazioni count (solo per appuntamenti validi)
    prestazioni_count = {}
    for app in appointments_validi:
        for prest in app.get("prestazioni", []):
            prestazioni_count[prest] = prestazioni_count.get(prest, 0) + 1
    
    # Monthly breakdown (solo per appuntamenti validi)
    monthly_stats = {}
    for app in appointments_validi:
        month = app["data"][:7]  # YYYY-MM
        if month not in monthly_stats:
            monthly_stats[month] = {"accessi": 0, "pazienti": set(), "prestazioni": {}}
        monthly_stats[month]["accessi"] += 1
        monthly_stats[month]["pazienti"].add(app["patient_id"])
        for prest in app.get("prestazioni", []):
            monthly_stats[month]["prestazioni"][prest] = monthly_stats[month]["prestazioni"].get(prest, 0) + 1
    
    # Convert sets to counts
    for month in monthly_stats:
        monthly_stats[month]["pazienti_unici"] = len(monthly_stats[month]["pazienti"])
        del monthly_stats[month]["pazienti"]
    
    return {
        "anno": anno,
        "mese": mese,
        "ambulatorio": ambulatorio.value,
        "tipo": tipo,
        "totale_accessi": total_accessi,
        "pazienti_unici": unique_patients,
        "prestazioni": prestazioni_count,
        "dettaglio_mensile": monthly_stats
    }

@api_router.get("/statistics/compare")
async def compare_statistics(
    ambulatorio: Ambulatorio,
    periodo1_anno: int,
    periodo1_mese: Optional[int] = None,
    periodo2_anno: int = None,
    periodo2_mese: Optional[int] = None,
    tipo: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Get stats for both periods
    stats1 = await get_statistics(ambulatorio, periodo1_anno, periodo1_mese, tipo, payload)
    stats2 = await get_statistics(ambulatorio, periodo2_anno or periodo1_anno, periodo2_mese, tipo, payload)
    
    # Calculate differences
    diff = {
        "accessi": stats2["totale_accessi"] - stats1["totale_accessi"],
        "pazienti_unici": stats2["pazienti_unici"] - stats1["pazienti_unici"],
        "prestazioni": {}
    }
    
    all_prestazioni = set(stats1["prestazioni"].keys()) | set(stats2["prestazioni"].keys())
    for prest in all_prestazioni:
        val1 = stats1["prestazioni"].get(prest, 0)
        val2 = stats2["prestazioni"].get(prest, 0)
        diff["prestazioni"][prest] = val2 - val1
    
    return {
        "periodo1": stats1,
        "periodo2": stats2,
        "differenze": diff
    }

# ============== CALENDAR HELPERS ==============
@api_router.get("/calendar/holidays")
async def get_calendar_holidays(anno: int):
    return get_holidays(anno)

@api_router.get("/calendar/slots")
async def get_time_slots():
    """Returns available time slots"""
    morning_slots = []
    afternoon_slots = []
    
    # Morning: 08:30 - 13:00
    current = datetime.strptime("08:30", "%H:%M")
    end_morning = datetime.strptime("13:00", "%H:%M")
    while current < end_morning:
        morning_slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)
    
    # Afternoon: 15:00 - 17:00
    current = datetime.strptime("15:00", "%H:%M")
    end_afternoon = datetime.strptime("17:00", "%H:%M")
    while current < end_afternoon:
        afternoon_slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)
    
    return {
        "mattina": morning_slots,
        "pomeriggio": afternoon_slots,
        "tutti": morning_slots + afternoon_slots
    }

# ============== DELETE ENDPOINTS ==============

@api_router.delete("/schede-impianto-picc/{scheda_id}")
async def delete_scheda_impianto(scheda_id: str, payload: dict = Depends(verify_token)):
    scheda = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.schede_impianto_picc.delete_one({"id": scheda_id})
    return {"message": "Scheda impianto eliminata"}

@api_router.delete("/schede-gestione-picc/{scheda_id}")
async def delete_scheda_gestione(scheda_id: str, payload: dict = Depends(verify_token)):
    scheda = await db.schede_gestione_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.schede_gestione_picc.delete_one({"id": scheda_id})
    return {"message": "Scheda gestione eliminata"}

@api_router.delete("/schede-medicazione-med/{scheda_id}")
async def delete_scheda_medicazione(scheda_id: str, payload: dict = Depends(verify_token)):
    scheda = await db.schede_medicazione_med.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    await db.schede_medicazione_med.delete_one({"id": scheda_id})
    return {"message": "Scheda medicazione eliminata"}

@api_router.put("/schede-impianto-picc/{scheda_id}")
async def update_scheda_impianto(scheda_id: str, data: dict, payload: dict = Depends(verify_token)):
    scheda = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    if not scheda:
        raise HTTPException(status_code=404, detail="Scheda non trovata")
    if scheda["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    await db.schede_impianto_picc.update_one({"id": scheda_id}, {"$set": data})
    updated = await db.schede_impianto_picc.find_one({"id": scheda_id}, {"_id": 0})
    return updated

# ============== IMPLANT STATISTICS ==============
@api_router.get("/statistics/implants")
async def get_implant_statistics(
    ambulatorio: Ambulatorio,
    anno: int,
    mese: Optional[int] = None,
    payload: dict = Depends(verify_token)
):
    """Get statistics for implants (PICC, Port, Midline, etc.)"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Build date range query
    if mese:
        start_date = f"{anno}-{mese:02d}-01"
        if mese == 12:
            end_date = f"{anno + 1}-01-01"
        else:
            end_date = f"{anno}-{mese + 1:02d}-01"
    else:
        start_date = f"{anno}-01-01"
        end_date = f"{anno + 1}-01-01"
    
    # Query implants
    query = {
        "ambulatorio": ambulatorio.value,
        "data_impianto": {"$gte": start_date, "$lt": end_date}
    }
    
    schede = await db.schede_impianto_picc.find(query, {"_id": 0}).to_list(1000)
    
    # Get list of existing patient IDs
    existing_patients = await db.patients.distinct("id", {"ambulatorio": ambulatorio.value})
    existing_patient_ids = set(existing_patients)
    
    # Count by type - only for existing patients
    tipo_counts = {}
    monthly_breakdown = {}
    
    for scheda in schede:
        # Skip if patient no longer exists
        patient_id = scheda.get("patient_id")
        if patient_id and patient_id not in existing_patient_ids:
            continue
            
        tipo = scheda.get("tipo_catetere", "altro")
        tipo_counts[tipo] = tipo_counts.get(tipo, 0) + 1
        
        # Monthly breakdown
        data_impianto = scheda.get("data_impianto", "")
        if data_impianto:
            month_key = data_impianto[:7]  # "YYYY-MM"
            if month_key not in monthly_breakdown:
                monthly_breakdown[month_key] = {}
            monthly_breakdown[month_key][tipo] = monthly_breakdown[month_key].get(tipo, 0) + 1
    
    # Labels for types
    tipo_labels = {
        "picc": "PICC",
        "picc_port": "PICC/Port",
        "midline": "Midline",
        "cvd_non_tunnellizzato": "CVC non tunnellizzato",
        "cvd_tunnellizzato": "CVC tunnellizzato",
        "port": "PORT",
    }
    
    return {
        "totale_impianti": len(schede),
        "per_tipo": tipo_counts,
        "tipo_labels": tipo_labels,
        "dettaglio_mensile": monthly_breakdown
    }

@api_router.get("/statistics/espianti")
async def get_espianti_statistics(
    ambulatorio: Ambulatorio,
    anno: int,
    mese: Optional[int] = None,
    payload: dict = Depends(verify_token)
):
    """Get statistics for espianti (from appointments with espianto prestazioni)"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Build date range query
    if mese:
        start_date = f"{anno}-{mese:02d}-01"
        if mese == 12:
            end_date = f"{anno + 1}-01-01"
        else:
            end_date = f"{anno}-{mese + 1:02d}-01"
    else:
        start_date = f"{anno}-01-01"
        end_date = f"{anno + 1}-01-01"
    
    # Query appointments with espianto prestazioni
    espianto_types = ["espianto_picc", "espianto_picc_port", "espianto_midline"]
    
    query = {
        "ambulatorio": ambulatorio.value,
        "data": {"$gte": start_date, "$lt": end_date},
        "prestazioni": {"$in": espianto_types}
    }
    
    appointments = await db.appointments.find(query, {"_id": 0}).to_list(10000)
    
    # Count by type
    tipo_counts = {
        "espianto_picc": 0,
        "espianto_picc_port": 0,
        "espianto_midline": 0
    }
    monthly_breakdown = {}
    
    for apt in appointments:
        prestazioni = apt.get("prestazioni", [])
        data = apt.get("data", "")
        month_key = data[:7] if data else ""
        
        for espianto_type in espianto_types:
            if espianto_type in prestazioni:
                tipo_counts[espianto_type] += 1
                
                if month_key:
                    if month_key not in monthly_breakdown:
                        monthly_breakdown[month_key] = {}
                    monthly_breakdown[month_key][espianto_type] = monthly_breakdown[month_key].get(espianto_type, 0) + 1
    
    # Labels
    tipo_labels = {
        "espianto_picc": "Espianto PICC",
        "espianto_picc_port": "Espianto PICC Port",
        "espianto_midline": "Espianto Midline"
    }
    
    totale = sum(tipo_counts.values())
    
    return {
        "totale_espianti": totale,
        "per_tipo": tipo_counts,
        "tipo_labels": tipo_labels,
        "dettaglio_mensile": monthly_breakdown
    }

# ============== PATIENT FOLDER DOWNLOAD ==============

def generate_patient_pdf_section(patient: dict, schede_med: list, schede_impianto: list, schede_gestione: list, section: str = "all") -> bytes:
    """Generate PDF for a specific section of the patient folder
    section: 'all', 'anagrafica', 'medicazione', 'impianto', 'gestione'
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=18, spaceAfter=30, alignment=TA_CENTER)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=14, spaceAfter=12, textColor=colors.HexColor('#1e40af'))
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=11, spaceAfter=6)
    
    story = []
    
    # Title based on section
    section_titles = {
        "all": "Cartella Clinica Completa",
        "anagrafica": "Anagrafica e Anamnesi",
        "medicazione": "Schede Medicazione",
        "impianto": "Schede Impianto PICC"
    }
    title = f"{section_titles.get(section, 'Cartella Clinica')} - {patient.get('cognome', '')} {patient.get('nome', '')}"
    story.append(Paragraph(title, title_style))
    story.append(Spacer(1, 20))
    
    # SEZIONE ANAGRAFICA - always show for 'all' and 'anagrafica'
    if section in ["all", "anagrafica"]:
        story.append(Paragraph("Dati Anagrafici", heading_style))
        info_data = [
            ["Nome:", patient.get('nome', '-')],
            ["Cognome:", patient.get('cognome', '-')],
            ["Tipo:", patient.get('tipo', '-')],
            ["Codice Fiscale:", patient.get('codice_fiscale', '-')],
            ["Data di Nascita:", patient.get('data_nascita', '-')],
            ["Sesso:", patient.get('sesso', '-')],
            ["Telefono:", patient.get('telefono', '-')],
            ["Email:", patient.get('email', '-')],
            ["Medico di Base:", patient.get('medico_base', '-')],
            ["Stato:", patient.get('status', '-')],
        ]
        table = Table(info_data, colWidths=[4*cm, 12*cm])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(table)
        story.append(Spacer(1, 20))
        
        # Anamnesi section
        if patient.get('anamnesi') or patient.get('terapia_in_atto') or patient.get('allergie'):
            story.append(Paragraph("Anamnesi", heading_style))
            if patient.get('anamnesi'):
                story.append(Paragraph(f"<b>Anamnesi:</b> {patient.get('anamnesi', '-')}", normal_style))
            if patient.get('terapia_in_atto'):
                story.append(Paragraph(f"<b>Terapia in Atto:</b> {patient.get('terapia_in_atto', '-')}", normal_style))
            if patient.get('allergie'):
                story.append(Paragraph(f"<b>Allergie:</b> {patient.get('allergie', '-')}", normal_style))
            story.append(Spacer(1, 20))
    
    # SEZIONE MEDICAZIONE - show for 'all' and 'medicazione'
    if section in ["all", "medicazione"] and schede_med:
        story.append(Paragraph("Schede Medicazione MED", heading_style))
        for idx, scheda in enumerate(schede_med, 1):
            story.append(Paragraph(f"<b>Medicazione #{idx} - Data: {scheda.get('data_compilazione', '-')}</b>", normal_style))
            story.append(Paragraph(f"Fondo: {', '.join(scheda.get('fondo', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Margini: {', '.join(scheda.get('margini', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Cute perilesionale: {', '.join(scheda.get('cute_perilesionale', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Essudato Quantità: {scheda.get('essudato_quantita', '-')}", normal_style))
            story.append(Paragraph(f"Essudato Tipo: {', '.join(scheda.get('essudato_tipo', [])) or '-'}", normal_style))
            if scheda.get('medicazione'):
                story.append(Paragraph(f"Medicazione: {scheda.get('medicazione', '-')}", normal_style))
            if scheda.get('prossimo_cambio'):
                story.append(Paragraph(f"Prossimo Cambio: {scheda.get('prossimo_cambio', '-')}", normal_style))
            if scheda.get('firma'):
                story.append(Paragraph(f"Firma Operatore: {scheda.get('firma', '-')}", normal_style))
            story.append(Spacer(1, 15))
        story.append(Spacer(1, 10))
    
    # SEZIONE IMPIANTO PICC - show for 'all' and 'impianto' (only complete)
    if section in ["all", "impianto"]:
        schede_complete = [s for s in schede_impianto if s.get('scheda_type') == 'completa']
        if schede_complete:
            story.append(Paragraph("Schede Impianto PICC (Complete)", heading_style))
            
            for idx, scheda in enumerate(schede_complete, 1):
                if idx > 1:
                    from reportlab.platypus import PageBreak
                    story.append(PageBreak())
                
                story.append(Paragraph(f"<b>Scheda Impianto #{idx}</b>", normal_style))
                story.append(Spacer(1, 10))
                
                def cb(checked):
                    return "[X]" if checked else "[  ]"
                
                def get_scheda_val(key, default=""):
                    val = scheda.get(key)
                    return default if val is None else val
                
                # Header info
                data_impianto = scheda.get('data_posizionamento') or scheda.get('data_impianto') or '-'
                story.append(Paragraph(f"<b>Data Impianto:</b> {data_impianto}", normal_style))
                story.append(Paragraph(f"<b>Presidio:</b> {get_scheda_val('presidio_ospedaliero', '-')}", normal_style))
                story.append(Paragraph(f"<b>U.O.:</b> {get_scheda_val('unita_operativa', '-')}", normal_style))
                story.append(Spacer(1, 10))
                
                # Tipo catetere
                tipo_opts = [
                    ("cvc_non_tunnellizzato", "CVC non tunnellizzato"),
                    ("cvc_tunnellizzato", "CVC tunnellizzato"),
                    ("picc", "PICC"),
                    ("port", "PORT"),
                    ("midline", "Midline"),
                ]
                tipo = get_scheda_val('tipo_catetere')
                tipo_line = "<b>TIPO DI CATETERE:</b> " + "  ".join([f"{cb(tipo == opt[0])} {opt[1]}" for opt in tipo_opts])
                story.append(Paragraph(tipo_line, normal_style))
                
                # Posizionamento
                braccio = get_scheda_val('braccio')
                vena = get_scheda_val('vena')
                pos_line = f"<b>POSIZIONAMENTO:</b> {cb(braccio == 'dx')} Braccio Dx  {cb(braccio == 'sn')} Braccio Sn    "
                pos_line += f"<b>Vena:</b> {cb(vena == 'basilica')} Basilica  {cb(vena == 'cefalica')} Cefalica  {cb(vena == 'brachiale')} Brachiale"
                story.append(Paragraph(pos_line, normal_style))
                story.append(Paragraph(f"<b>Exit-site:</b> {get_scheda_val('exit_site_cm', '-')} cm", normal_style))
                story.append(Spacer(1, 8))
                
                # Procedure con checkbox SI/NO
                procedures = [
                    ('valutazione_sito', 'VALUTAZIONE MIGLIOR SITO DI INSERIMENTO'),
                    ('ecoguidato', 'IMPIANTO ECOGUIDATO'),
                    ('igiene_mani', 'IGIENE DELLE MANI'),
                    ('precauzioni_barriera', 'UTILIZZO MASSIME PRECAUZIONI DI BARRIERA'),
                ]
                for key, label in procedures:
                    val = get_scheda_val(key)
                    line = f"<b>{label}:</b>  {cb(val == True)} SI  {cb(val == False)} NO"
                    story.append(Paragraph(line, normal_style))
                
                # Disinfezione
                disinfezione = get_scheda_val('disinfezione') or []
                dis_line = f"<b>DISINFEZIONE:</b>  {cb('clorexidina_2' in disinfezione)} Clorexidina 2%  {cb('iodiopovidone' in disinfezione)} Iodiopovidone"
                story.append(Paragraph(dis_line, normal_style))
                
                # Dispositivi
                dispositivi = [
                    ('sutureless_device', 'SUTURELESS DEVICE'),
                    ('medicazione_trasparente', 'MEDICAZIONE TRASPARENTE'),
                    ('medicazione_occlusiva', 'MEDICAZIONE OCCLUSIVA'),
                    ('controllo_rx', 'CONTROLLO RX POST-INSERIMENTO'),
                    ('controllo_ecg', 'CONTROLLO ECG POST-INSERIMENTO'),
                ]
                for key, label in dispositivi:
                    val = get_scheda_val(key)
                    line = f"<b>{label}:</b>  {cb(val == True)} SI  {cb(val == False)} NO"
                    story.append(Paragraph(line, normal_style))
                
                # Modalità
                modalita = get_scheda_val('modalita')
                mod_line = f"<b>MODALITÀ:</b>  {cb(modalita == 'emergenza')} EMERGENZA  {cb(modalita == 'urgenza')} URGENZA  {cb(modalita == 'elezione')} ELEZIONE"
                story.append(Paragraph(mod_line, normal_style))
                
                # Motivazione
                motivazione = get_scheda_val('motivazione') or []
                motiv_opts = [("chemioterapia", "Chemioterapia"), ("difficolta_vene", "Difficoltà vene"), 
                              ("terapia_prolungata", "Terapia prolungata"), ("monitoraggio", "Monitoraggio")]
                motiv_line = "<b>MOTIVAZIONE:</b>  " + "  ".join([f"{cb(m[0] in motivazione)} {m[1]}" for m in motiv_opts])
                story.append(Paragraph(motiv_line, normal_style))
                
                # Operatore
                story.append(Spacer(1, 8))
                story.append(Paragraph(f"<b>OPERATORE:</b> {get_scheda_val('operatore', '-')}", normal_style))
                
                if scheda.get('note'):
                    story.append(Paragraph(f"<b>Note:</b> {scheda.get('note', '')}", normal_style))
                
                story.append(Spacer(1, 15))
            story.append(Spacer(1, 10))
        
        # Gestione PICC (monthly)
        if schede_gestione:
            story.append(Spacer(1, 20))
            story.append(Paragraph("Schede Gestione PICC (Accessi Venosi)", heading_style))
            
            gestione_items = [
                ("data_giorno_mese", "Data (giorno/mese)"),
                ("uso_precauzioni_barriera", "Uso massime precauzioni barriera"),
                ("lavaggio_mani", "Lavaggio mani"),
                ("guanti_non_sterili", "Uso guanti non sterili"),
                ("cambio_guanti_sterili", "Cambio guanti con guanti sterili"),
                ("rimozione_medicazione_sutureless", "Rimozione medicazione e sostituzione sutureless"),
                ("rimozione_medicazione_straordinaria", "Rimozione medicazione ord/straordinaria"),
                ("ispezione_sito", "Ispezione del sito"),
                ("sito_dolente", "Sito dolente"),
                ("edema_arrossamento", "Presenza di edema/arrossamento"),
                ("disinfezione_sito", "Disinfezione del sito"),
                ("exit_site_cm", "Exit-site cm"),
                ("fissaggio_sutureless", "Fissaggio catetere con sutureless device"),
                ("medicazione_trasparente", "Medicazione semipermeabile trasparente"),
                ("lavaggio_fisiologica", "Lavaggio con fisiologica 10cc/20cc"),
                ("disinfezione_clorexidina", "Disinfezione Clorexidina 2%"),
                ("difficolta_aspirazione", "Difficoltà di aspirazione"),
                ("difficolta_iniezione", "Difficoltà iniezione"),
                ("medicazione_clorexidina_prolungato", "Medicazione Clorexidina rilascio prol."),
                ("port_protector", "Utilizzo Port Protector"),
                ("lock_eparina", "Lock eparina per lavaggi"),
                ("sostituzione_set", "Sostituzione set infusione"),
                ("ore_sostituzione_set", "Ore da precedente sostituzione set"),
                ("febbre", "Febbre"),
                ("emocoltura", "Prelievo emocoltura"),
                ("emocoltura_positiva", "Emocoltura positiva per CVC"),
                ("trasferimento", "Trasferimento altra struttura"),
                ("rimozione_cvc", "Rimozione CVC"),
                ("sigla_operatore", "SIGLA OPERATORE"),
            ]
            
            for scheda in schede_gestione:
                story.append(Paragraph(f"<b>Mese: {scheda.get('mese', '-')}</b>", normal_style))
                giorni = scheda.get('giorni', {})
                
                if giorni:
                    sorted_dates = sorted(giorni.keys())
                    
                    for chunk_start in range(0, len(sorted_dates), 10):
                        chunk_dates = sorted_dates[chunk_start:chunk_start + 10]
                        header_row = ["Attività"] + [d.split("-")[-1] for d in chunk_dates]
                        table_data = [header_row]
                        
                        for item_id, item_label in gestione_items:
                            row = [item_label]
                            for date_str in chunk_dates:
                                val = giorni.get(date_str, {}).get(item_id, "-")
                                row.append(val if val else "-")
                            table_data.append(row)
                        
                        col_widths = [5*cm] + [1.2*cm] * len(chunk_dates)
                        table = Table(table_data, colWidths=col_widths)
                        table.setStyle(TableStyle([
                            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                            ('FONTNAME', (0, 1), (0, -1), 'Helvetica'),
                            ('FONTSIZE', (0, 0), (-1, -1), 6),
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#166534')),
                            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                            ('TOPPADDING', (0, 0), (-1, -1), 2),
                            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')]),
                        ]))
                        story.append(table)
                        story.append(Spacer(1, 10))
                    
                    if scheda.get('note'):
                        story.append(Paragraph(f"<b>Note:</b> {scheda.get('note', '')}", normal_style))
                else:
                    story.append(Paragraph("Nessuna medicazione registrata per questo mese.", normal_style))
                
                story.append(Spacer(1, 15))
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_patient_pdf(patient: dict, schede_med: list, schede_impianto: list, schede_gestione: list, photos: list) -> bytes:
    """Generate a PDF with patient data - NO allegati, NO foto in scheda MED, only COMPLETE scheda impianto"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=18, spaceAfter=30, alignment=TA_CENTER)
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading2'], fontSize=14, spaceAfter=12, textColor=colors.HexColor('#1e40af'))
    normal_style = ParagraphStyle('CustomNormal', parent=styles['Normal'], fontSize=11, spaceAfter=6)
    
    story = []
    
    # Title
    story.append(Paragraph(f"Cartella Clinica - {patient.get('cognome', '')} {patient.get('nome', '')}", title_style))
    story.append(Spacer(1, 20))
    
    # SEZIONE 1: Dati Anagrafici
    story.append(Paragraph("Dati Anagrafici", heading_style))
    info_data = [
        ["Nome:", patient.get('nome', '-')],
        ["Cognome:", patient.get('cognome', '-')],
        ["Tipo:", patient.get('tipo', '-')],
        ["Codice Fiscale:", patient.get('codice_fiscale', '-')],
        ["Data di Nascita:", patient.get('data_nascita', '-')],
        ["Sesso:", patient.get('sesso', '-')],
        ["Telefono:", patient.get('telefono', '-')],
        ["Email:", patient.get('email', '-')],
        ["Medico di Base:", patient.get('medico_base', '-')],
        ["Stato:", patient.get('status', '-')],
    ]
    table = Table(info_data, colWidths=[4*cm, 12*cm])
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 20))
    
    # SEZIONE 2: Anamnesi
    if patient.get('anamnesi') or patient.get('terapia_in_atto') or patient.get('allergie'):
        story.append(Paragraph("Anamnesi", heading_style))
        if patient.get('anamnesi'):
            story.append(Paragraph(f"<b>Anamnesi:</b> {patient.get('anamnesi', '-')}", normal_style))
        if patient.get('terapia_in_atto'):
            story.append(Paragraph(f"<b>Terapia in Atto:</b> {patient.get('terapia_in_atto', '-')}", normal_style))
        if patient.get('allergie'):
            story.append(Paragraph(f"<b>Allergie:</b> {patient.get('allergie', '-')}", normal_style))
        story.append(Spacer(1, 20))
    
    # SEZIONE 3: Schede Medicazione MED (senza anagrafica, senza foto)
    if schede_med:
        story.append(Paragraph("Schede Medicazione MED", heading_style))
        for idx, scheda in enumerate(schede_med, 1):
            story.append(Paragraph(f"<b>Medicazione #{idx} - Data: {scheda.get('data_compilazione', '-')}</b>", normal_style))
            story.append(Paragraph(f"Fondo: {', '.join(scheda.get('fondo', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Margini: {', '.join(scheda.get('margini', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Cute perilesionale: {', '.join(scheda.get('cute_perilesionale', [])) or '-'}", normal_style))
            story.append(Paragraph(f"Essudato Quantità: {scheda.get('essudato_quantita', '-')}", normal_style))
            story.append(Paragraph(f"Essudato Tipo: {', '.join(scheda.get('essudato_tipo', [])) or '-'}", normal_style))
            if scheda.get('medicazione'):
                story.append(Paragraph(f"Medicazione: {scheda.get('medicazione', '-')}", normal_style))
            if scheda.get('prossimo_cambio'):
                story.append(Paragraph(f"Prossimo Cambio: {scheda.get('prossimo_cambio', '-')}", normal_style))
            if scheda.get('firma'):
                story.append(Paragraph(f"Firma Operatore: {scheda.get('firma', '-')}", normal_style))
            # NOTE: NO foto qui - le foto rimangono solo nell'applicativo
            story.append(Spacer(1, 15))
        story.append(Spacer(1, 10))
    
    # SEZIONE 4: Schede Impianto PICC - SOLO schede COMPLETE nel formato ufficiale
    schede_complete = [s for s in schede_impianto if s.get('scheda_type') == 'completa']
    if schede_complete:
        story.append(Paragraph("Schede Impianto PICC (Complete)", heading_style))
        
        for idx, scheda in enumerate(schede_complete, 1):
            # Aggiungi PageBreak se non è la prima scheda
            if idx > 1:
                story.append(PageBreak())
            
            # Usa lo stesso formato del PDF singolo
            story.append(Paragraph(f"<b>Scheda Impianto #{idx}</b>", normal_style))
            story.append(Spacer(1, 10))
            
            # Helper functions for this scheda
            def cb(checked):
                if checked:
                    return "[X]"
                else:
                    return "[  ]"
            
            def get_scheda_val(key, default=""):
                val = scheda.get(key)
                if val is None:
                    return default
                return val
            
            # Header info
            data_impianto = scheda.get('data_posizionamento') or scheda.get('data_impianto') or '-'
            story.append(Paragraph(f"<b>Data Impianto:</b> {data_impianto}", normal_style))
            story.append(Paragraph(f"<b>Presidio:</b> {get_scheda_val('presidio_ospedaliero', '-')}", normal_style))
            story.append(Paragraph(f"<b>U.O.:</b> {get_scheda_val('unita_operativa', '-')}", normal_style))
            story.append(Spacer(1, 10))
            
            # Tipo catetere
            tipo_opts = [
                ("cvc_non_tunnellizzato", "CVC non tunnellizzato"),
                ("cvc_tunnellizzato", "CVC tunnellizzato"),
                ("picc", "PICC"),
                ("port", "PORT"),
                ("midline", "Midline"),
            ]
            tipo = get_scheda_val('tipo_catetere')
            tipo_line = "<b>TIPO DI CATETERE:</b> " + "  ".join([f"{cb(tipo == opt[0])} {opt[1]}" for opt in tipo_opts])
            story.append(Paragraph(tipo_line, normal_style))
            
            # Posizionamento
            braccio = get_scheda_val('braccio')
            vena = get_scheda_val('vena')
            pos_line = f"<b>POSIZIONAMENTO:</b> {cb(braccio == 'dx')} Braccio Dx  {cb(braccio == 'sn')} Braccio Sn    "
            pos_line += f"<b>Vena:</b> {cb(vena == 'basilica')} Basilica  {cb(vena == 'cefalica')} Cefalica  {cb(vena == 'brachiale')} Brachiale"
            story.append(Paragraph(pos_line, normal_style))
            story.append(Paragraph(f"<b>Exit-site:</b> {get_scheda_val('exit_site_cm', '-')} cm", normal_style))
            story.append(Spacer(1, 8))
            
            # Procedure con checkbox SI/NO
            procedures = [
                ('valutazione_sito', 'VALUTAZIONE MIGLIOR SITO DI INSERIMENTO'),
                ('ecoguidato', 'IMPIANTO ECOGUIDATO'),
                ('igiene_mani', 'IGIENE DELLE MANI'),
                ('precauzioni_barriera', 'UTILIZZO MASSIME PRECAUZIONI DI BARRIERA'),
            ]
            for key, label in procedures:
                val = get_scheda_val(key)
                line = f"<b>{label}:</b>  {cb(val == True)} SI  {cb(val == False)} NO"
                story.append(Paragraph(line, normal_style))
            
            # Disinfezione
            disinfezione = get_scheda_val('disinfezione') or []
            dis_line = f"<b>DISINFEZIONE:</b>  {cb('clorexidina_2' in disinfezione)} Clorexidina 2%  {cb('iodiopovidone' in disinfezione)} Iodiopovidone"
            story.append(Paragraph(dis_line, normal_style))
            
            # Dispositivi
            dispositivi = [
                ('sutureless_device', 'SUTURELESS DEVICE'),
                ('medicazione_trasparente', 'MEDICAZIONE TRASPARENTE'),
                ('medicazione_occlusiva', 'MEDICAZIONE OCCLUSIVA'),
                ('controllo_rx', 'CONTROLLO RX POST-INSERIMENTO'),
                ('controllo_ecg', 'CONTROLLO ECG POST-INSERIMENTO'),
            ]
            for key, label in dispositivi:
                val = get_scheda_val(key)
                line = f"<b>{label}:</b>  {cb(val == True)} SI  {cb(val == False)} NO"
                story.append(Paragraph(line, normal_style))
            
            # Modalità
            modalita = get_scheda_val('modalita')
            mod_line = f"<b>MODALITÀ:</b>  {cb(modalita == 'emergenza')} EMERGENZA  {cb(modalita == 'urgenza')} URGENZA  {cb(modalita == 'elezione')} ELEZIONE"
            story.append(Paragraph(mod_line, normal_style))
            
            # Motivazione
            motivazione = get_scheda_val('motivazione') or []
            motiv_opts = [("chemioterapia", "Chemioterapia"), ("difficolta_vene", "Difficoltà vene"), 
                          ("terapia_prolungata", "Terapia prolungata"), ("monitoraggio", "Monitoraggio")]
            motiv_line = "<b>MOTIVAZIONE:</b>  " + "  ".join([f"{cb(m[0] in motivazione)} {m[1]}" for m in motiv_opts])
            story.append(Paragraph(motiv_line, normal_style))
            
            # Operatore
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"<b>OPERATORE:</b> {get_scheda_val('operatore', '-')}", normal_style))
            
            if scheda.get('note'):
                story.append(Paragraph(f"<b>Note:</b> {scheda.get('note', '')}", normal_style))
            
            story.append(Spacer(1, 15))
        story.append(Spacer(1, 10))
    
    # PICC Gestione Schede (Monthly Management)
    if schede_gestione:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Schede Gestione PICC (Accessi Venosi)", heading_style))
        
        # Define the items to display
        gestione_items = [
            ("data_giorno_mese", "Data (giorno/mese)"),
            ("uso_precauzioni_barriera", "Uso massime precauzioni barriera"),
            ("lavaggio_mani", "Lavaggio mani"),
            ("guanti_non_sterili", "Uso guanti non sterili"),
            ("cambio_guanti_sterili", "Cambio guanti con guanti sterili"),
            ("rimozione_medicazione_sutureless", "Rimozione medicazione e sostituzione sutureless"),
            ("rimozione_medicazione_straordinaria", "Rimozione medicazione ord/straordinaria"),
            ("ispezione_sito", "Ispezione del sito"),
            ("sito_dolente", "Sito dolente"),
            ("edema_arrossamento", "Presenza di edema/arrossamento"),
            ("disinfezione_sito", "Disinfezione del sito"),
            ("exit_site_cm", "Exit-site cm"),
            ("fissaggio_sutureless", "Fissaggio catetere con sutureless device"),
            ("medicazione_trasparente", "Medicazione semipermeabile trasparente"),
            ("lavaggio_fisiologica", "Lavaggio con fisiologica 10cc/20cc"),
            ("disinfezione_clorexidina", "Disinfezione Clorexidina 2%"),
            ("difficolta_aspirazione", "Difficoltà di aspirazione"),
            ("difficolta_iniezione", "Difficoltà iniezione"),
            ("medicazione_clorexidina_prolungato", "Medicazione Clorexidina rilascio prol."),
            ("port_protector", "Utilizzo Port Protector"),
            ("lock_eparina", "Lock eparina per lavaggi"),
            ("sostituzione_set", "Sostituzione set infusione"),
            ("ore_sostituzione_set", "Ore da precedente sostituzione set"),
            ("febbre", "Febbre"),
            ("emocoltura", "Prelievo emocoltura"),
            ("emocoltura_positiva", "Emocoltura positiva per CVC"),
            ("trasferimento", "Trasferimento altra struttura"),
            ("rimozione_cvc", "Rimozione CVC"),
            ("sigla_operatore", "SIGLA OPERATORE"),
        ]
        
        for scheda in schede_gestione:
            story.append(Paragraph(f"<b>Mese: {scheda.get('mese', '-')}</b>", normal_style))
            giorni = scheda.get('giorni', {})
            
            if giorni:
                # Sort dates
                sorted_dates = sorted(giorni.keys())
                num_cols = min(len(sorted_dates), 10)  # Max 10 columns per table for readability
                
                # Split into chunks if more than 10 dates
                for chunk_start in range(0, len(sorted_dates), 10):
                    chunk_dates = sorted_dates[chunk_start:chunk_start + 10]
                    
                    # Build header row
                    header_row = ["Attività"] + [d.split("-")[-1] for d in chunk_dates]  # Show day number
                    
                    # Build data rows
                    table_data = [header_row]
                    for item_id, item_label in gestione_items:
                        row = [item_label]
                        for date_str in chunk_dates:
                            val = giorni.get(date_str, {}).get(item_id, "-")
                            row.append(val if val else "-")
                        table_data.append(row)
                    
                    # Create table
                    col_widths = [5*cm] + [1.2*cm] * len(chunk_dates)
                    table = Table(table_data, colWidths=col_widths)
                    table.setStyle(TableStyle([
                        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                        ('FONTNAME', (0, 1), (0, -1), 'Helvetica'),
                        ('FONTSIZE', (0, 0), (-1, -1), 6),
                        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#166534')),
                        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f0f0f0')]),
                    ]))
                    story.append(table)
                    story.append(Spacer(1, 10))
                
                # Add notes if present
                if scheda.get('note'):
                    story.append(Paragraph(f"<b>Note:</b> {scheda.get('note', '')}", normal_style))
            else:
                story.append(Paragraph("Nessuna medicazione registrata per questo mese.", normal_style))
            
            story.append(Spacer(1, 15))
    
    # NOTE: Allegati section removed from PDF download as per user request
    # Gli allegati NON vengono scaricati con la cartella paziente
    
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_patient_zip(patient: dict, schede_med: list, schede_impianto: list, schede_gestione: list, photos: list) -> bytes:
    """Generate a ZIP with patient data - NO allegati, only PDF cartella clinica"""
    buffer = io.BytesIO()
    
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Add PDF summary (NO allegati, NO foto MED)
        pdf_data = generate_patient_pdf(patient, schede_med, schede_impianto, schede_gestione, [])
        zf.writestr(f"cartella_clinica_{patient.get('cognome', 'paziente')}_{patient.get('nome', '')}.pdf", pdf_data)
        
        # Add patient JSON data
        import json
        patient_json = json.dumps(patient, indent=2, ensure_ascii=False)
        zf.writestr("dati_paziente.json", patient_json)
        
        # Add MED schede as JSON (without photos)
        if schede_med:
            med_json = json.dumps(schede_med, indent=2, ensure_ascii=False)
            zf.writestr("schede_medicazione_med.json", med_json)
        
        # Add only COMPLETE PICC impianto schede as JSON (no semplificata)
        schede_complete = [s for s in schede_impianto if s.get('scheda_type') != 'semplificata']
        if schede_complete:
            impianto_json = json.dumps(schede_complete, indent=2, ensure_ascii=False)
            zf.writestr("schede_impianto_picc.json", impianto_json)
        
        # Add PICC gestione schede as JSON
        if schede_gestione:
            gestione_json = json.dumps(schede_gestione, indent=2, ensure_ascii=False)
            zf.writestr("schede_gestione_picc.json", gestione_json)
        
        # NOTE: NO allegati nel ZIP - si scaricano singolarmente
    
    buffer.seek(0)
    return buffer.getvalue()


@api_router.get("/patients/{patient_id}/download/pdf")
async def download_patient_pdf(patient_id: str, section: str = "all", payload: dict = Depends(verify_token)):
    """Download patient folder as PDF - with optional section filter
    section: 'all', 'anagrafica', 'medicazione', 'impianto', 'gestione'
    """
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    if patient["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Fetch data based on section
    schede_med = []
    schede_impianto = []
    schede_gestione = []
    
    if section in ["all", "medicazione"]:
        schede_med = await db.schede_medicazione_med.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    
    if section in ["all", "impianto"]:
        schede_impianto = await db.schede_impianto_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    
    if section in ["all", "gestione"]:
        schede_gestione = await db.schede_gestione_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    
    # Generate PDF with the appropriate section
    pdf_data = generate_patient_pdf_section(patient, schede_med, schede_impianto, schede_gestione, section)
    
    section_names = {"all": "completa", "anagrafica": "anagrafica", "medicazione": "medicazione", "impianto": "impianto", "gestione": "gestione_picc"}
    section_name = section_names.get(section, section)
    filename = f"cartella_{section_name}_{patient.get('cognome', 'paziente')}_{patient.get('nome', '')}.pdf"
    
    return StreamingResponse(
        io.BytesIO(pdf_data),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@api_router.get("/patients/{patient_id}/download/zip")
async def download_patient_zip(patient_id: str, payload: dict = Depends(verify_token)):
    """Download patient folder as ZIP - NO allegati (si scaricano singolarmente)"""
    patient = await db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        raise HTTPException(status_code=404, detail="Paziente non trovato")
    if patient["ambulatorio"] not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    # Fetch all related data - NO photos (allegati si scaricano separatamente)
    schede_med = await db.schede_medicazione_med.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    schede_impianto = await db.schede_impianto_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    schede_gestione = await db.schede_gestione_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
    
    zip_data = generate_patient_zip(patient, schede_med, schede_impianto, schede_gestione, [])
    
    filename = f"cartella_{patient.get('cognome', 'paziente')}_{patient.get('nome', '')}.zip"
    
    return StreamingResponse(
        io.BytesIO(zip_data),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# ============== ROOT ==============
@api_router.get("/")
async def root():
    return {"message": "Ambulatorio Infermieristico API", "version": "1.0.0"}

# ============== PRESCRIZIONI ==============
class PrescrizioneCreate(BaseModel):
    patient_id: str
    ambulatorio: Ambulatorio
    data_inizio: str  # YYYY-MM-DD
    durata_mesi: int = 1  # 1, 2, or 3 months

class Prescrizione(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    ambulatorio: Ambulatorio
    data_inizio: str
    durata_mesi: int
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@api_router.get("/prescrizioni")
async def get_prescrizioni(
    ambulatorio: Ambulatorio,
    current_user: dict = Depends(get_current_user)
):
    """Get all prescriptions for an ambulatorio"""
    cursor = db.prescrizioni.find({"ambulatorio": ambulatorio})
    prescrizioni = await cursor.to_list(length=1000)
    result = []
    for p in prescrizioni:
        item = {
            "id": p.get("id", str(p.get("_id", ""))),
            "patient_id": p.get("patient_id"),
            "ambulatorio": p.get("ambulatorio"),
            "data_inizio": p.get("data_inizio"),
            "durata_mesi": p.get("durata_mesi"),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at")
        }
        result.append(item)
    return result

@api_router.post("/prescrizioni")
async def create_or_update_prescrizione(
    data: PrescrizioneCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create or update a prescription for a patient"""
    # Check if prescription already exists for this patient
    existing = await db.prescrizioni.find_one({
        "patient_id": data.patient_id,
        "ambulatorio": data.ambulatorio
    })
    
    if existing:
        # Update existing
        await db.prescrizioni.update_one(
            {"_id": existing["_id"]},
            {
                "$set": {
                    "data_inizio": data.data_inizio,
                    "durata_mesi": data.durata_mesi,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        return {"message": "Prescrizione aggiornata", "id": existing.get("id")}
    else:
        # Create new
        prescrizione = Prescrizione(
            patient_id=data.patient_id,
            ambulatorio=data.ambulatorio,
            data_inizio=data.data_inizio,
            durata_mesi=data.durata_mesi
        )
        await db.prescrizioni.insert_one(prescrizione.model_dump())
        return {"message": "Prescrizione creata", "id": prescrizione.id}

@api_router.delete("/prescrizioni/{patient_id}")
async def delete_prescrizione(
    patient_id: str,
    ambulatorio: Ambulatorio,
    current_user: dict = Depends(get_current_user)
):
    """Delete a prescription for a patient"""
    result = await db.prescrizioni.delete_one({
        "patient_id": patient_id,
        "ambulatorio": ambulatorio
    })
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Prescrizione non trovata")
    return {"message": "Prescrizione eliminata"}

# ============== AI ASSISTANT ==============
from emergentintegrations.llm.chat import LlmChat, UserMessage
import json
import re

# AI Chat history storage in MongoDB
class AIChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    user_id: str
    ambulatorio: str
    role: str  # "user" or "assistant"
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class AIChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    ambulatorio: Ambulatorio

class AIChatResponse(BaseModel):
    response: str
    session_id: str
    action_performed: Optional[dict] = None

# ============== SISTEMA UNDO PER IA ==============
class UndoAction(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    ambulatorio: str
    action_type: str
    action_description: str
    undo_data: dict  # Dati necessari per annullare l'azione
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

async def save_undo_action(user_id: str, ambulatorio: str, action_type: str, description: str, undo_data: dict):
    """Salva un'azione per poterla annullare successivamente"""
    action = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "ambulatorio": ambulatorio,
        "action_type": action_type,
        "action_description": description,
        "undo_data": undo_data,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await db.ai_undo_history.insert_one(action)
    
    # Mantieni solo le ultime 10 azioni per utente/ambulatorio
    actions = await db.ai_undo_history.find(
        {"user_id": user_id, "ambulatorio": ambulatorio}
    ).sort("timestamp", -1).to_list(100)
    
    if len(actions) > 10:
        # Elimina le azioni più vecchie oltre le 10
        old_ids = [a["id"] for a in actions[10:]]
        await db.ai_undo_history.delete_many({"id": {"$in": old_ids}})
    
    return action["id"]

async def get_undo_actions(user_id: str, ambulatorio: str, limit: int = 10):
    """Ottiene le ultime azioni annullabili"""
    return await db.ai_undo_history.find(
        {"user_id": user_id, "ambulatorio": ambulatorio},
        {"_id": 0}
    ).sort("timestamp", -1).to_list(limit)

async def execute_undo(action: dict, ambulatorio: str) -> dict:
    """Esegue l'annullamento di un'azione"""
    action_type = action["action_type"]
    undo_data = action["undo_data"]
    
    try:
        if action_type == "create_patient":
            # Annulla creazione = elimina paziente
            patient_id = undo_data.get("patient_id")
            await db.patients.delete_one({"id": patient_id})
            return {"success": True, "message": f"↩️ Annullato: Paziente eliminato"}
        
        elif action_type == "delete_patient":
            # Annulla eliminazione = ricrea paziente e dati
            patient_data = undo_data.get("patient_data")
            appointments = undo_data.get("appointments", [])
            schede_impianto = undo_data.get("schede_impianto", [])
            schede_gestione = undo_data.get("schede_gestione", [])
            schede_med = undo_data.get("schede_med", [])
            prescrizioni = undo_data.get("prescrizioni", [])
            
            if patient_data:
                await db.patients.insert_one(patient_data)
            for apt in appointments:
                await db.appointments.insert_one(apt)
            for s in schede_impianto:
                await db.schede_impianto_picc.insert_one(s)
            for s in schede_gestione:
                await db.schede_gestione_picc.insert_one(s)
            for s in schede_med:
                await db.schede_medicazione_med.insert_one(s)
            for p in prescrizioni:
                await db.prescrizioni.insert_one(p)
            
            nome = f"{patient_data.get('cognome', '')} {patient_data.get('nome', '')}"
            return {"success": True, "message": f"↩️ Annullato: Paziente **{nome}** ripristinato con tutti i dati"}
        
        elif action_type in ["suspend_patient", "resume_patient", "discharge_patient"]:
            # Annulla cambio stato = ripristina stato precedente
            patient_id = undo_data.get("patient_id")
            previous_status = undo_data.get("previous_status")
            previous_data = undo_data.get("previous_data", {})
            
            update_data = {"status": previous_status, "updated_at": datetime.now(timezone.utc).isoformat()}
            if "data_dimissione" in previous_data:
                update_data["data_dimissione"] = previous_data["data_dimissione"]
            
            await db.patients.update_one({"id": patient_id}, {"$set": update_data})
            
            patient = await db.patients.find_one({"id": patient_id})
            nome = f"{patient.get('cognome', '')} {patient.get('nome', '')}" if patient else "Paziente"
            return {"success": True, "message": f"↩️ Annullato: **{nome}** tornato a stato '{previous_status}'"}
        
        elif action_type == "create_appointment":
            # Annulla creazione appuntamento = elimina
            appointment_id = undo_data.get("appointment_id")
            await db.appointments.delete_one({"id": appointment_id})
            return {"success": True, "message": f"↩️ Annullato: Appuntamento eliminato"}
        
        elif action_type == "delete_appointment":
            # Annulla eliminazione appuntamento = ricrea
            appointment_data = undo_data.get("appointment_data")
            if appointment_data:
                await db.appointments.insert_one(appointment_data)
            return {"success": True, "message": f"↩️ Annullato: Appuntamento ripristinato"}
        
        elif action_type == "create_scheda_impianto":
            # Annulla creazione scheda = elimina
            scheda_id = undo_data.get("scheda_id")
            await db.schede_impianto_picc.delete_one({"id": scheda_id})
            return {"success": True, "message": f"↩️ Annullato: Scheda impianto eliminata"}
        
        elif action_type == "copy_scheda_med":
            # Annulla copia scheda MED = elimina la nuova
            scheda_id = undo_data.get("scheda_id")
            await db.schede_medicazione_med.delete_one({"id": scheda_id})
            return {"success": True, "message": f"↩️ Annullato: Scheda MED copiata eliminata"}
        
        elif action_type == "copy_scheda_gestione_picc":
            # Annulla copia giorno PICC = rimuovi il giorno aggiunto
            scheda_id = undo_data.get("scheda_id")
            day_key = undo_data.get("day_key")
            await db.schede_gestione_picc.update_one(
                {"id": scheda_id},
                {"$unset": {f"giorni.{day_key}": ""}}
            )
            return {"success": True, "message": f"↩️ Annullato: Giorno {day_key} rimosso dalla scheda"}
        
        elif action_type == "create_multiple_patients":
            # Annulla creazione multipla = elimina tutti i pazienti creati
            patient_ids = undo_data.get("patient_ids", [])
            for pid in patient_ids:
                await db.patients.delete_one({"id": pid})
            return {"success": True, "message": f"↩️ Annullato: {len(patient_ids)} pazienti eliminati"}
        
        elif action_type == "suspend_multiple_patients":
            # Annulla sospensione multipla = ripristina stati precedenti
            patients_data = undo_data.get("patients_data", [])
            for pd in patients_data:
                await db.patients.update_one(
                    {"id": pd["patient_id"]},
                    {"$set": {"status": pd["previous_status"], "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
            return {"success": True, "message": f"↩️ Annullato: {len(patients_data)} pazienti ripristinati allo stato precedente"}
        
        elif action_type == "resume_multiple_patients":
            # Annulla ripresa multipla = ripristina stati precedenti
            patients_data = undo_data.get("patients_data", [])
            for pd in patients_data:
                await db.patients.update_one(
                    {"id": pd["patient_id"]},
                    {"$set": {"status": pd["previous_status"], "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
            return {"success": True, "message": f"↩️ Annullato: {len(patients_data)} pazienti ripristinati allo stato precedente"}
        
        elif action_type == "discharge_multiple_patients":
            # Annulla dimissione multipla = ripristina stati precedenti
            patients_data = undo_data.get("patients_data", [])
            for pd in patients_data:
                update_data = {"status": pd["previous_status"], "updated_at": datetime.now(timezone.utc).isoformat()}
                if "data_dimissione" in pd.get("previous_data", {}):
                    update_data["data_dimissione"] = pd["previous_data"]["data_dimissione"]
                await db.patients.update_one({"id": pd["patient_id"]}, {"$set": update_data})
            return {"success": True, "message": f"↩️ Annullato: {len(patients_data)} pazienti ripristinati allo stato precedente"}
        
        elif action_type == "delete_multiple_patients":
            # Annulla eliminazione multipla = ricrea tutti i pazienti con i loro dati
            all_backup_data = undo_data.get("all_backup_data", [])
            restored_count = 0
            for backup in all_backup_data:
                patient_data = backup.get("patient_data")
                if patient_data:
                    await db.patients.insert_one(patient_data)
                    restored_count += 1
                for apt in backup.get("appointments", []):
                    await db.appointments.insert_one(apt)
                for s in backup.get("schede_impianto", []):
                    await db.schede_impianto_picc.insert_one(s)
                for s in backup.get("schede_gestione", []):
                    await db.schede_gestione_picc.insert_one(s)
                for s in backup.get("schede_med", []):
                    await db.schede_medicazione_med.insert_one(s)
                for p in backup.get("prescrizioni", []):
                    await db.prescrizioni.insert_one(p)
            return {"success": True, "message": f"↩️ Annullato: {restored_count} pazienti ripristinati con tutti i loro dati"}
        
        return {"success": False, "message": "❌ Tipo di azione non supportato per l'annullamento"}
        
    except Exception as e:
        logger.error(f"Undo error: {str(e)}")
        return {"success": False, "message": f"❌ Errore nell'annullamento: {str(e)}"}

SYSTEM_PROMPT = """Sei un assistente IA dell'Ambulatorio Infermieristico. Il tuo compito è eseguire ESATTAMENTE le istruzioni dell'utente.

DATA ODIERNA: {today}

⚠️ REGOLA FONDAMENTALE - ORARI:
**USA SEMPRE L'ORARIO ESATTO SPECIFICATO DALL'UTENTE!**
- Se l'utente dice "ore 13" → usa "13:00" (NON 09:00, NON 09:30, NON altro!)
- Se l'utente dice "ore 12" → usa "12:00"
- Se l'utente dice "ore 9" → usa "09:00"
- Se l'utente dice "ore 15:30" → usa "15:30"
- MAI cambiare l'orario specificato dall'utente!
- L'orario va inserito ESATTAMENTE come richiesto nel campo "ora" del JSON

CAPACITÀ:
1. **Gestire pazienti**: Creare, cercare, aprire, sospendere, riprendere in cura, dimettere, eliminare
2. **Gestire appuntamenti**: Creare ed eliminare appuntamenti dall'agenda
3. **Statistiche**: Consultare statistiche
4. **Generare PDF**: Statistiche, cartelle pazienti
5. **Compilare schede**: Creare e copiare schede MED e PICC
6. **Annullare azioni**: "annulla" o "undo"

ORARI DISPONIBILI:
- MATTINA: 08:30, 09:00, 09:30, 10:00, 10:30, 11:00, 11:30, 12:00, 12:30, 13:00, 13:30
- POMERIGGIO: 15:00, 15:30, 16:00, 16:30, 17:00, 17:30
- 13:00 e 13:30 sono MATTINA
- Max 2 pazienti per slot

STATI PAZIENTE: in_cura, sospeso, dimesso

REGOLE:
- Rispondi in italiano
- **ORARIO**: USA ESATTAMENTE l'orario specificato dall'utente, SENZA MODIFICARLO!
- Se un orario è occupato, CHIEDI all'utente quale altro orario preferisce
- I tipi paziente sono: PICC, MED, PICC_MED
- **RICERCA PAZIENTE**: Usa prima il COGNOME, poi il nome

FORMATO RISPOSTA:
Per azioni, rispondi SOLO con JSON: {"action": "...", "params": {...}, "message": "..."}}

AZIONI DISPONIBILI:

=== GESTIONE SINGOLO PAZIENTE ===
- create_patient: {"nome": "...", "cognome": "...", "tipo": "PICC/MED/PICC_MED"}
- suspend_patient: {"patient_name": "cognome nome"} - Sospende temporaneamente il paziente
- resume_patient: {"patient_name": "cognome nome"} - Riprende in cura il paziente sospeso
- discharge_patient: {"patient_name": "cognome nome"} - Dimette il paziente
- delete_patient: {"patient_name": "cognome nome"} - Elimina definitivamente il paziente
- open_patient: {"patient_name": "cognome nome"}
- search_patient: {"query": "..."}

=== GESTIONE MULTIPLA PAZIENTI (BATCH) ===
- create_multiple_patients: {"patients": [{{"nome": "...", "cognome": "...", "tipo": "PICC/MED/PICC_MED"}, ...]}}
- suspend_multiple_patients: {"patient_names": ["cognome nome", "cognome2 nome2", ...]}
- resume_multiple_patients: {"patient_names": ["cognome nome", "cognome2 nome2", ...]}
- discharge_multiple_patients: {"patient_names": ["cognome nome", "cognome2 nome2", ...]}
- delete_multiple_patients: {"patient_names": ["cognome nome", "cognome2 nome2", ...]}

=== ESTRAZIONE DA FOTO ===
- extract_patients_from_image: {} - Richiede immagine allegata, estrae nomi pazienti
- add_extracted_patients: {"patients": [{{"nome": "...", "cognome": "...", "tipo": "PICC/MED"}], "tipo_default": "PICC/MED"}}

=== APPUNTAMENTI ===
- create_appointment: {"patient_name": "cognome nome", "data": "YYYY-MM-DD", "ora": "HH:MM"}
  ⚠️ IMPORTANTE: Usa ESATTAMENTE l'orario specificato dall'utente nel campo "ora"!
  Es: utente dice "ore 13" → usa "ora": "13:00"
- delete_appointment: {"patient_name": "cognome nome", "data": "YYYY-MM-DD", "ora": "HH:MM"}

=== STATISTICHE ===
- get_patients_count: {"tipo": "PICC/MED/PICC_MED/tutti", "stato": "in_cura/sospeso/dimesso/tutti"} - Conta pazienti per tipo e stato
- get_implant_statistics: {"tipo_impianto": "picc/midline/picc_port/port_a_cath/tutti", "anno": 2025, "mese": 1-12 o null, "generate_pdf": true/false}
- get_prestazioni_statistics: {"tipo": "PICC/MED/tutti", "anno": 2025, "mese": 1-12 o null, "generate_pdf": true/false}
- compare_statistics: {"tipo": "PICC/MED/IMPIANTI/tutti", "periodo1": {"anno": 2025, "mese": null}, "periodo2": {"anno": 2026, "mese": null}, "generate_pdf": true/false}

=== SCHEDE ===
- create_scheda_impianto: {"patient_name": "cognome nome", "tipo_catetere": "picc/midline/picc_port/port_a_cath", "data_impianto": "YYYY-MM-DD"}
- copy_scheda_med: {"patient_name": "cognome nome", "nuova_data": "YYYY-MM-DD"}
- copy_scheda_gestione_picc: {"patient_name": "cognome nome", "nuova_data": "YYYY-MM-DD"}
- print_patient_folder: {"patient_name": "cognome nome", "sezione": "completa/anagrafica/impianto/gestione_picc/scheda_med"}

=== ANNULLA ===
- undo_action: {} - Annulla l'ultima azione (o {"action_id": "..."}} per annullare una specifica)
- list_undo_actions: {} - Mostra le ultime 10 azioni annullabili

ESEMPI ORARI (SEGUI ESATTAMENTE):
- "Appuntamento Bianchi Lucia 12/01/26 ore 13" → {"action": "create_appointment", "params": {"patient_name": "Bianchi Lucia", "data": "2026-01-12", "ora": "13:00"}, "message": "Creo appuntamento per ore 13:00"}
- "Rossi domani alle 15" → {"action": "create_appointment", "params": {"patient_name": "Rossi", "data": "...", "ora": "15:00"}, "message": "..."}
- "Verdi ore 9:30" → {"action": "create_appointment", "params": {"patient_name": "Verdi", "data": "...", "ora": "09:30"}, "message": "..."}
- "Mario Bianchi appuntamento 13:30" → {"action": "create_appointment", "params": {"patient_name": "Bianchi Mario", "data": "...", "ora": "13:30"}, "message": "..."}

ALTRI ESEMPI:
- "Crea i pazienti: Rossi Mario PICC, Bianchi Luigi MED" → {"action": "create_multiple_patients", "params": {"patients": [{"cognome": "Rossi", "nome": "Mario", "tipo": "PICC"}, {"cognome": "Bianchi", "nome": "Luigi", "tipo": "MED"}]}, "message": "Creo i pazienti..."}
- "Sospendi Rossi, Bianchi e Verdi" → {"action": "suspend_multiple_patients", "params": {"patient_names": ["Rossi", "Bianchi", "Verdi"]}, "message": "Sospendo i pazienti..."}
- "Annulla" → {"action": "undo_action", "params": {}, "message": "Annullo..."}
- "Quanti pazienti PICC ho?" → {"action": "get_patients_count", "params": {"tipo": "PICC", "stato": "in_cura"}, "message": "..."}

Per domande generiche (es. "Ciao"), rispondi normalmente senza JSON."""

async def get_ai_response(message: str, session_id: str, ambulatorio: str, user_id: str) -> dict:
    """Get AI response using emergentintegrations"""
    try:
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            return {"response": "Errore: chiave API non configurata", "action": None}
        
        # Get chat history from database
        history = await db.ai_chat_history.find({
            "session_id": session_id
        }).sort("timestamp", 1).to_list(50)
        
        # Build conversation context
        context_messages = []
        for msg in history[-10:]:  # Last 10 messages
            context_messages.append(f"{msg['role'].upper()}: {msg['content']}")
        
        context = "\n".join(context_messages)
        full_message = f"{context}\n\nUSER: {message}" if context else message
        
        # Format system prompt with today's date
        today = datetime.now().strftime("%Y-%m-%d")
        formatted_prompt = SYSTEM_PROMPT.replace("{today}", today)
        
        # Initialize chat
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message=formatted_prompt
        ).with_model("openai", "gpt-4o")
        
        user_msg = UserMessage(text=full_message)
        response = await chat.send_message(user_msg)
        
        # Parse response for actions
        action = None
        response_text = response
        
        # Check if response contains JSON action - improved regex for nested objects
        # First try to find JSON block with code fence
        code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if code_block_match:
            try:
                action = json.loads(code_block_match.group(1))
                response_text = action.get("message", response)
            except json.JSONDecodeError:
                pass
        
        # If no code block, try to find raw JSON
        if not action:
            # Find JSON that starts with {"action" and contains nested params
            json_match = re.search(r'(\{[^{}]*"action"[^{}]*"params"\s*:\s*\{[^{}]*\}[^{}]*\})', response, re.DOTALL)
            if json_match:
                try:
                    action = json.loads(json_match.group(1))
                    response_text = action.get("message", response)
                except json.JSONDecodeError:
                    pass
        
        # If the entire response is a JSON
        if not action and response.strip().startswith('{'):
            try:
                action = json.loads(response.strip())
                response_text = action.get("message", response)
            except json.JSONDecodeError:
                pass
        
        return {"response": response_text, "action": action}
        
    except Exception as e:
        logger.error(f"AI Error: {str(e)}")
        return {"response": f"Mi dispiace, ho avuto un problema: {str(e)}", "action": None}

async def execute_ai_action(action: dict, ambulatorio: str, user_id: str) -> dict:
    """Execute an action determined by AI - VERSIONE COMPLETA"""
    action_type = action.get("action")
    params = action.get("params", {})
    
    # Helper per trovare paziente
    async def find_patient(patient_name: str):
        """
        Ricerca paziente migliorata con matching più preciso.
        Priorità: match esatto cognome > match esatto nome > match parziale
        """
        name_lower = patient_name.lower().strip()
        parts = [p.strip() for p in name_lower.split() if len(p.strip()) > 1]
        
        if not parts:
            return None
        
        projection = {"_id": 0}  # Escludi sempre _id
        
        # 1. Prima prova match esatto su cognome (primo termine)
        if len(parts) >= 1:
            # Prova il primo termine come cognome esatto
            exact_match = await db.patients.find_one({
                "ambulatorio": ambulatorio,
                "cognome": {"$regex": f"^{parts[0]}$", "$options": "i"}
            }, projection)
            if exact_match:
                # Se c'è un secondo termine, verifica che corrisponda al nome
                if len(parts) >= 2:
                    nome_lower = exact_match.get("nome", "").lower()
                    if parts[1] in nome_lower or nome_lower.startswith(parts[1]):
                        return exact_match
                else:
                    return exact_match
        
        # 2. Prova match esatto cognome + nome insieme
        if len(parts) >= 2:
            # Cognome esatto + nome che inizia con
            exact_match = await db.patients.find_one({
                "ambulatorio": ambulatorio,
                "cognome": {"$regex": f"^{parts[0]}$", "$options": "i"},
                "nome": {"$regex": f"^{parts[1]}", "$options": "i"}
            }, projection)
            if exact_match:
                return exact_match
            
            # Prova invertito (nome cognome invece di cognome nome)
            exact_match = await db.patients.find_one({
                "ambulatorio": ambulatorio,
                "cognome": {"$regex": f"^{parts[1]}$", "$options": "i"},
                "nome": {"$regex": f"^{parts[0]}", "$options": "i"}
            }, projection)
            if exact_match:
                return exact_match
        
        # 3. Match parziale su cognome (per abbreviazioni)
        partial_match = await db.patients.find_one({
            "ambulatorio": ambulatorio,
            "cognome": {"$regex": f"^{parts[0]}", "$options": "i"}
        }, projection)
        if partial_match:
            return partial_match
        
        # 4. Ultima risorsa: cerca in tutti i campi ma con AND invece di OR
        # Tutti i termini devono essere presenti nel cognome+nome combinato
        pipeline = [
            {"$match": {"ambulatorio": ambulatorio}},
            {"$addFields": {
                "full_name": {"$concat": [{"$toLower": "$cognome"}, " ", {"$toLower": "$nome"}]}
            }},
            {"$match": {
                "$and": [{"full_name": {"$regex": part, "$options": "i"}} for part in parts]
            }},
            {"$project": {"_id": 0, "full_name": 0}},  # Escludi _id e campo temporaneo
            {"$limit": 1}
        ]
        
        results = await db.patients.aggregate(pipeline).to_list(1)
        if results:
            return results[0]
        
        return None
    
    # Helper per trovare primo slot disponibile
    async def find_available_slot(data: str, tipo: str, turno: str = "primo_disponibile"):
        slots_mattina = ["08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00", "13:30"]
        slots_pomeriggio = ["15:00", "15:30", "16:00", "16:30", "17:00", "17:30"]
        
        if turno == "mattina":
            slots = slots_mattina
        elif turno == "pomeriggio":
            slots = slots_pomeriggio
        else:
            slots = slots_mattina + slots_pomeriggio
        
        for slot in slots:
            count = await db.appointments.count_documents({
                "ambulatorio": ambulatorio,
                "data": data,
                "ora": slot,
                "tipo": tipo
            })
            if count < 2:
                return slot
        return None
    
    # Nomi mesi per messaggi
    MESI = ["", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", 
            "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
    
    try:
        # ==================== UNDO ACTION ====================
        if action_type == "undo_action":
            action_id = params.get("action_id")
            
            if action_id:
                # Annulla azione specifica
                undo_action_data = await db.ai_undo_history.find_one({"id": action_id, "user_id": user_id, "ambulatorio": ambulatorio})
            else:
                # Annulla ultima azione
                undo_action_data = await db.ai_undo_history.find_one(
                    {"user_id": user_id, "ambulatorio": ambulatorio},
                    sort=[("timestamp", -1)]
                )
            
            if not undo_action_data:
                return {"success": False, "message": "❌ Nessuna azione da annullare"}
            
            # Esegui l'annullamento
            result = await execute_undo(undo_action_data, ambulatorio)
            
            # Rimuovi l'azione dallo storico
            if result.get("success"):
                await db.ai_undo_history.delete_one({"id": undo_action_data["id"]})
            
            return result
        
        # ==================== LIST UNDO ACTIONS ====================
        elif action_type == "list_undo_actions":
            actions = await get_undo_actions(user_id, ambulatorio, 10)
            
            if not actions:
                return {"success": True, "message": "📋 Nessuna azione annullabile disponibile.\n\nLe azioni vengono salvate quando crei, modifichi o elimini pazienti, appuntamenti e schede."}
            
            msg = "📋 **Ultime azioni annullabili:**\n\n"
            for i, action in enumerate(actions, 1):
                timestamp = datetime.fromisoformat(action["timestamp"].replace("Z", "+00:00"))
                time_str = timestamp.strftime("%d/%m %H:%M")
                msg += f"{i}. {action['action_description']} ({time_str})\n"
            
            msg += "\n💡 Scrivi **'annulla'** per annullare l'ultima azione, oppure **'annulla azione 3'** per annullare una specifica."
            
            return {"success": True, "actions": actions, "message": msg}
        
        # ==================== CREATE PATIENT ====================
        elif action_type == "create_patient":
            patient_data = {
                "id": str(uuid.uuid4()),
                "nome": params.get("nome", ""),
                "cognome": params.get("cognome", ""),
                "tipo": params.get("tipo", "PICC"),
                "ambulatorio": ambulatorio,
                "status": "in_cura",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.patients.insert_one(patient_data)
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "create_patient",
                f"Creato paziente {params.get('cognome')} {params.get('nome')}",
                {"patient_id": patient_data["id"]}
            )
            
            return {"success": True, "patient_id": patient_data["id"], 
                    "message": f"✅ Paziente **{params.get('cognome')} {params.get('nome')}** creato con successo come {params.get('tipo', 'PICC')}!\n\n💡 Puoi annullare questa azione dicendo 'annulla'",
                    "can_undo": True}
        
        # ==================== SEARCH PATIENT ====================
        elif action_type == "search_patient":
            query = params.get("query", "").strip()
            
            # Prova prima con find_patient che ha logica migliorata
            patient = await find_patient(query)
            if patient:
                patient_info = {"id": patient["id"], "cognome": patient.get("cognome", ""), "nome": patient.get("nome", ""), "tipo": patient.get("tipo", "")}
                return {"success": True, "patients": [patient], 
                        "patient": patient_info,
                        "action_type": "search_patient",
                        "message": f"🔍 Trovato: **{patient['cognome']} {patient['nome']}** ({patient['tipo']})\n\n💡 Cosa vuoi fare con questo paziente?"}
            
            # Se non trova esatto, cerca parziale
            query_lower = query.lower()
            parts = [p.strip() for p in query_lower.split() if len(p.strip()) > 1]
            
            # Costruisci query che cerca tutte le parti
            if parts:
                or_conditions = []
                for part in parts:
                    or_conditions.append({"nome": {"$regex": part, "$options": "i"}})
                    or_conditions.append({"cognome": {"$regex": part, "$options": "i"}})
                
                patients = await db.patients.find({
                    "ambulatorio": ambulatorio,
                    "$or": or_conditions
                }, {"_id": 0}).to_list(10)
            else:
                patients = []
            
            if patients:
                names = [f"• {p['cognome']} {p['nome']} ({p['tipo']})" for p in patients]
                if len(patients) == 1:
                    patient_info = {"id": patients[0]["id"], "cognome": patients[0].get("cognome", ""), "nome": patients[0].get("nome", ""), "tipo": patients[0].get("tipo", "")}
                    return {"success": True, "patients": patients, 
                            "patient": patient_info,
                            "action_type": "search_patient",
                            "message": f"🔍 Trovato: **{patients[0]['cognome']} {patients[0]['nome']}** ({patients[0]['tipo']})\n\n💡 Cosa vuoi fare con questo paziente?"}
                return {"success": True, "patients": patients, 
                        "message": f"🔍 Ho trovato {len(patients)} pazienti:\n" + "\n".join(names) + "\n\n💡 Specifica quale paziente ti interessa."}
            return {"success": False, "message": f"❌ Nessun paziente trovato con '{query}'"}
        
        # ==================== CREATE APPOINTMENT ====================
        elif action_type == "create_appointment":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato. Vuoi che lo crei?"}
            
            data = params.get("data")
            ora = params.get("ora")  # L'orario ESATTO specificato dall'utente
            turno = params.get("turno")
            tipo = params.get("tipo", patient.get("tipo", "PICC"))
            if tipo == "PICC_MED":
                tipo = "PICC"
            
            # IMPORTANTE: Se l'utente ha specificato un orario, usalo SEMPRE
            # Cerca slot solo se NON c'è un orario specificato
            if not ora:
                # Nessun orario specificato, cerca il primo disponibile
                search_turno = turno if turno in ["mattina", "pomeriggio"] else "primo_disponibile"
                ora = await find_available_slot(data, tipo, search_turno)
                if not ora:
                    turno_msg = f" del {turno}" if turno else ""
                    return {"success": False, "message": f"❌ Nessun orario disponibile{turno_msg} per il {data}. Vuoi provare un altro giorno?"}
            else:
                # L'utente ha specificato un orario - verifica disponibilità
                existing = await db.appointments.count_documents({
                    "ambulatorio": ambulatorio,
                    "data": data,
                    "ora": ora,
                    "tipo": tipo
                })
                
                if existing >= 2:
                    # Slot pieno - chiedi all'utente cosa fare
                    return {"success": False, 
                            "message": f"⚠️ Orario **{ora}** già occupato (2 pazienti).\n\nVuoi scegliere un altro orario?",
                            "suggested_data": data}
            
            # Crea appuntamento
            prestazioni = params.get("prestazioni", [])
            if not prestazioni:
                if tipo == "PICC":
                    prestazioni = ["medicazione_semplice", "irrigazione_catetere"]
                elif tipo == "MED":
                    prestazioni = ["medicazione_semplice"]
            
            appointment = {
                "id": str(uuid.uuid4()),
                "patient_id": patient["id"],
                "patient_nome": patient.get("nome", ""),
                "patient_cognome": patient.get("cognome", ""),
                "ambulatorio": ambulatorio,
                "data": data,
                "ora": ora,
                "tipo": tipo,
                "prestazioni": prestazioni,
                "stato": "da_fare",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await db.appointments.insert_one(appointment)
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "create_appointment",
                f"Creato appuntamento per {patient['cognome']} {patient['nome']} il {data} alle {ora}",
                {"appointment_id": appointment["id"]}
            )
            
            # Includi info paziente per memoria contestuale frontend
            patient_info = {"id": patient["id"], "cognome": patient.get("cognome", ""), "nome": patient.get("nome", ""), "tipo": patient.get("tipo", "")}
            
            return {"success": True, 
                    "message": f"✅ Appuntamento creato!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📅 {data} alle **{ora}**\n🏷️ Tipo: {tipo}\n\n💡 Puoi annullare dicendo 'annulla'",
                    "can_undo": True,
                    "patient": patient_info,
                    "action_type": "create_appointment"}
        
        # ==================== DELETE APPOINTMENT ====================
        elif action_type == "delete_appointment":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            data = params.get("data")
            ora = params.get("ora")
            
            query = {
                "ambulatorio": ambulatorio,
                "patient_id": patient["id"],
                "data": data
            }
            if ora:
                query["ora"] = ora
            
            # Trova l'appuntamento
            appointment = await db.appointments.find_one(query)
            if not appointment:
                return {"success": False, "message": f"❌ Nessun appuntamento trovato per {patient['cognome']} {patient['nome']} il {data}"}
            
            # Salva per undo (copia i dati dell'appuntamento)
            appointment_copy = {k: v for k, v in appointment.items() if k != "_id"}
            await save_undo_action(
                user_id, ambulatorio, "delete_appointment",
                f"Eliminato appuntamento di {patient['cognome']} {patient['nome']} del {data}",
                {"appointment_data": appointment_copy}
            )
            
            # Elimina
            await db.appointments.delete_one({"id": appointment["id"]})
            return {"success": True, 
                    "message": f"✅ Appuntamento eliminato!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📅 {data} alle {appointment.get('ora', 'N/A')}\n\n💡 Puoi annullare dicendo 'annulla'",
                    "can_undo": True}
        
        # ==================== GET PATIENTS COUNT ====================
        elif action_type == "get_patients_count":
            tipo = params.get("tipo", "tutti")
            stato = params.get("stato", "tutti")
            
            query = {"ambulatorio": ambulatorio}
            
            # Filtra per tipo
            if tipo and tipo != "tutti":
                query["tipo"] = tipo
            
            # Filtra per stato
            if stato and stato != "tutti":
                query["status"] = stato
            
            # Conta pazienti
            patients = await db.patients.find(query, {"_id": 0, "id": 1, "tipo": 1, "status": 1}).to_list(10000)
            total = len(patients)
            
            # Conta per tipo
            tipo_counts = {}
            stato_counts = {}
            for p in patients:
                t = p.get("tipo", "non_specificato")
                s = p.get("status", "in_cura")
                tipo_counts[t] = tipo_counts.get(t, 0) + 1
                stato_counts[s] = stato_counts.get(s, 0) + 1
            
            tipo_labels = {"PICC": "PICC", "MED": "MED", "PICC_MED": "PICC+MED"}
            stato_labels = {"in_cura": "In cura", "sospeso": "Sospesi", "dimesso": "Dimessi"}
            
            msg = f"📊 **Conteggio Pazienti**\n\n"
            msg += f"📈 **Totale: {total}** pazienti"
            
            if tipo != "tutti":
                tipo_label = tipo_labels.get(tipo, tipo)
                msg += f" di tipo {tipo_label}"
            if stato != "tutti":
                stato_label = stato_labels.get(stato, stato)
                msg += f" ({stato_label})"
            
            msg += "\n\n"
            
            if tipo == "tutti":
                msg += "**Per tipo:**\n"
                for t, c in tipo_counts.items():
                    label = tipo_labels.get(t, t)
                    msg += f"🔹 {label}: {c}\n"
                msg += "\n"
            
            if stato == "tutti":
                msg += "**Per stato:**\n"
                for s, c in stato_counts.items():
                    label = stato_labels.get(s, s)
                    msg += f"🔹 {label}: {c}\n"
            
            return {
                "success": True, 
                "totale": total, 
                "per_tipo": tipo_counts,
                "per_stato": stato_counts,
                "message": msg
            }
        
        # ==================== GET IMPLANT STATISTICS ====================
        elif action_type == "get_implant_statistics":
            tipo_impianto = params.get("tipo_impianto", "tutti")
            anno = params.get("anno", datetime.now().year)
            mese = params.get("mese")
            generate_pdf = params.get("generate_pdf", False)
            
            # Build date range
            if mese:
                start_date = f"{anno}-{mese:02d}-01"
                end_date = f"{anno}-{mese + 1:02d}-01" if mese < 12 else f"{anno + 1}-01-01"
                periodo = f"{MESI[mese]} {anno}"
            else:
                start_date = f"{anno}-01-01"
                end_date = f"{anno + 1}-01-01"
                periodo = f"anno {anno}"
            
            query = {
                "ambulatorio": ambulatorio,
                "data_impianto": {"$gte": start_date, "$lt": end_date}
            }
            
            if tipo_impianto and tipo_impianto != "tutti":
                query["tipo_catetere"] = tipo_impianto
            
            schede = await db.schede_impianto_picc.find(query, {"_id": 0}).to_list(10000)
            
            # Conta per tipo
            tipo_counts = {}
            for s in schede:
                t = s.get("tipo_catetere", "non_specificato")
                tipo_counts[t] = tipo_counts.get(t, 0) + 1
            
            tipo_labels = {
                "picc": "PICC",
                "midline": "Midline", 
                "picc_port": "PICC Port",
                "port_a_cath": "Port-a-cath",
            }
            
            if tipo_impianto and tipo_impianto != "tutti":
                count = tipo_counts.get(tipo_impianto, 0)
                label = tipo_labels.get(tipo_impianto, tipo_impianto.upper())
                msg = f"📊 **Statistiche Impianti - {periodo}**\n\n"
                msg += f"🔹 **{label}**: {count} impianti\n"
            else:
                msg = f"📊 **Statistiche Impianti - {periodo}**\n\n"
                msg += f"📈 Totale: **{len(schede)}** impianti\n\n"
                for t, c in tipo_counts.items():
                    label = tipo_labels.get(t, t)
                    msg += f"🔹 {label}: {c}\n"
            
            if generate_pdf:
                msg += "\n\n📥 Clicca 'Scarica PDF' per il report."
            else:
                msg += "\n\nVuoi che generi il report PDF?"
            
            result = {"success": True, "totale": len(schede), "per_tipo": tipo_counts, 
                    "periodo": periodo, "message": msg, "offer_pdf": True}
            
            if generate_pdf:
                result["pdf_endpoint"] = f"/statistics/implants/pdf?ambulatorio={ambulatorio}&anno={anno}&mese={mese or ''}&tipo={tipo_impianto}"
                result["filename"] = f"impianti_{periodo.replace(' ', '_')}.pdf"
            
            return result
        
        # ==================== GET PRESTAZIONI STATISTICS ====================
        elif action_type == "get_prestazioni_statistics":
            tipo = params.get("tipo")
            anno = params.get("anno", datetime.now().year)
            mese = params.get("mese")
            generate_pdf = params.get("generate_pdf", False)
            
            if mese:
                start_date = f"{anno}-{mese:02d}-01"
                end_date = f"{anno}-{mese + 1:02d}-01" if mese < 12 else f"{anno + 1}-01-01"
                periodo = f"{MESI[mese]} {anno}"
            else:
                start_date = f"{anno}-01-01"
                end_date = f"{anno + 1}-01-01"
                periodo = f"anno {anno}"
            
            query = {
                "ambulatorio": ambulatorio,
                "data": {"$gte": start_date, "$lt": end_date},
                "stato": {"$ne": "non_presentato"}
            }
            if tipo and tipo != "tutti":
                query["tipo"] = tipo
            
            appointments = await db.appointments.find(query).to_list(10000)
            
            prestazioni_count = {}
            prestazioni_labels = {
                "medicazione_semplice": "Medicazione semplice",
                "irrigazione_catetere": "Irrigazione catetere",
                "fasciatura_semplice": "Fasciatura semplice",
                "iniezione_terapeutica": "Iniezione terapeutica",
                "catetere_vescicale": "Catetere vescicale"
            }
            
            for app in appointments:
                for prest in app.get("prestazioni", []):
                    prestazioni_count[prest] = prestazioni_count.get(prest, 0) + 1
            
            msg = f"📊 **Statistiche Prestazioni - {periodo}**\n\n"
            msg += f"📈 Totale accessi: **{len(appointments)}**\n"
            msg += f"👥 Pazienti unici: **{len(set(a['patient_id'] for a in appointments))}**\n\n"
            
            if prestazioni_count:
                msg += "**Dettaglio prestazioni:**\n"
                for p, c in prestazioni_count.items():
                    label = prestazioni_labels.get(p, p)
                    msg += f"🔹 {label}: {c}\n"
            else:
                msg += "Nessuna prestazione registrata.\n"
            
            if generate_pdf:
                msg += "\n\n📥 Clicca 'Scarica PDF' per il report."
            else:
                msg += "\nVuoi che generi il report PDF?"
            
            result = {"success": True, "totale_accessi": len(appointments),
                    "prestazioni": prestazioni_count, "periodo": periodo, 
                    "message": msg, "offer_pdf": True}
            
            if generate_pdf:
                result["pdf_endpoint"] = f"/statistics/pdf?ambulatorio={ambulatorio}&anno={anno}&mese={mese or ''}&tipo={tipo or 'tutti'}"
                result["filename"] = f"prestazioni_{periodo.replace(' ', '_')}.pdf"
            
            return result
        
        # ==================== COPY SCHEDA MED ====================
        elif action_type == "copy_scheda_med":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            # Trova ultima scheda MED
            last_scheda = await db.schede_medicazione_med.find_one(
                {"patient_id": patient["id"], "ambulatorio": ambulatorio},
                sort=[("created_at", -1)]
            )
            
            if not last_scheda:
                return {"success": False, "message": f"❌ Nessuna scheda MED precedente trovata per {patient['cognome']} {patient['nome']}"}
            
            # Copia con nuova data
            nuova_data = params.get("nuova_data", datetime.now().strftime("%Y-%m-%d"))
            new_scheda = {k: v for k, v in last_scheda.items() if k not in ["_id", "id", "created_at", "updated_at"]}
            new_scheda["id"] = str(uuid.uuid4())
            new_scheda["data_compilazione"] = nuova_data
            new_scheda["created_at"] = datetime.now(timezone.utc).isoformat()
            new_scheda["updated_at"] = datetime.now(timezone.utc).isoformat()
            
            await db.schede_medicazione_med.insert_one(new_scheda)
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "copy_scheda_med",
                f"Copiata scheda MED per {patient['cognome']} {patient['nome']}",
                {"scheda_id": new_scheda["id"]}
            )
            
            return {"success": True, 
                    "message": f"✅ Scheda MED copiata!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📅 Nuova data: {nuova_data}\n\nHo copiato tutti i dati dalla scheda precedente.\n\n💡 Puoi annullare dicendo 'annulla'",
                    "navigate_to": f"/pazienti/{patient['id']}",
                    "can_undo": True}
        
        # ==================== COPY SCHEDA GESTIONE PICC ====================
        elif action_type == "copy_scheda_gestione_picc":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            nuova_data = params.get("nuova_data", datetime.now().strftime("%Y-%m-%d"))
            
            # Trova ultima scheda gestione PICC
            last_scheda = await db.schede_gestione_picc.find_one(
                {"patient_id": patient["id"], "ambulatorio": ambulatorio},
                sort=[("created_at", -1)]
            )
            
            if not last_scheda:
                return {"success": False, "message": f"❌ Nessuna scheda gestione PICC precedente trovata per {patient['cognome']} {patient['nome']}"}
            
            # Trova l'ultimo giorno compilato nella scheda
            giorni = last_scheda.get("giorni", {})
            if not giorni:
                return {"success": False, "message": "❌ La scheda precedente non ha dati da copiare"}
            
            last_day_key = sorted(giorni.keys())[-1]
            last_day_data = giorni[last_day_key]
            
            # Aggiorna la data nel nuovo giorno
            day = int(nuova_data.split("-")[2])
            month = int(nuova_data.split("-")[1])
            new_day_data = {**last_day_data, "data_giorno_mese": f"{day}/{month}"}
            
            # Aggiungi il nuovo giorno alla scheda
            await db.schede_gestione_picc.update_one(
                {"id": last_scheda["id"]},
                {"$set": {f"giorni.{nuova_data}": new_day_data, "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "copy_scheda_gestione_picc",
                f"Copiata medicazione PICC per {patient['cognome']} {patient['nome']}",
                {"scheda_id": last_scheda["id"], "day_key": nuova_data}
            )
            
            # Includi info paziente per memoria contestuale frontend
            patient_info = {"id": patient["id"], "cognome": patient.get("cognome", ""), "nome": patient.get("nome", ""), "tipo": patient.get("tipo", "")}
            
            return {"success": True,
                    "message": f"✅ Medicazione PICC copiata!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📅 Nuova data: {nuova_data}\n\nHo copiato i dati dalla medicazione precedente ({last_day_key}).\n\n💡 Puoi annullare dicendo 'annulla'",
                    "navigate_to": f"/pazienti/{patient['id']}",
                    "can_undo": True,
                    "patient": patient_info,
                    "action_type": "copy_scheda_gestione_picc"}
        
        # ==================== OPEN PATIENT ====================
        elif action_type == "open_patient":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if patient:
                patient_info = {"id": patient["id"], "cognome": patient.get("cognome", ""), "nome": patient.get("nome", ""), "tipo": patient.get("tipo", "")}
                return {"success": True, 
                        "patient": patient_info, 
                        "navigate_to": f"/pazienti/{patient['id']}",
                        "message": f"📂 Apro la cartella di **{patient['cognome']} {patient['nome']}**...",
                        "action_type": "open_patient"}
            return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
        
        # ==================== CREATE SCHEDA IMPIANTO ====================
        elif action_type == "create_scheda_impianto":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            tipo_catetere = params.get("tipo_catetere", "picc")
            data_impianto = params.get("data_impianto", datetime.now().strftime("%Y-%m-%d"))
            
            scheda = {
                "id": str(uuid.uuid4()),
                "patient_id": patient["id"],
                "ambulatorio": ambulatorio,
                "scheda_type": "semplificata",
                "tipo_catetere": tipo_catetere,
                "data_impianto": data_impianto,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            await db.schede_impianto_picc.insert_one(scheda)
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "create_scheda_impianto",
                f"Creata scheda impianto per {patient['cognome']} {patient['nome']}",
                {"scheda_id": scheda["id"]}
            )
            
            tipo_labels = {"picc": "PICC", "midline": "Midline", "picc_port": "PICC Port", "port_a_cath": "Port-a-cath"}
            label = tipo_labels.get(tipo_catetere, tipo_catetere.upper())
            
            return {"success": True, 
                    "message": f"✅ Scheda impianto creata!\n\n👤 **{patient['cognome']} {patient['nome']}**\n🔹 Tipo: {label}\n📅 Data: {data_impianto}\n\n💡 Puoi annullare dicendo 'annulla'",
                    "navigate_to": f"/pazienti/{patient['id']}",
                    "can_undo": True}
        
        # ==================== GET STATISTICS (legacy) ====================
        elif action_type == "get_statistics":
            # Redirect to appropriate new action
            tipo = params.get("tipo")
            if tipo == "IMPIANTI":
                params["tipo_impianto"] = "tutti"
                return await execute_ai_action({"action": "get_implant_statistics", "params": params}, ambulatorio, user_id)
            else:
                return await execute_ai_action({"action": "get_prestazioni_statistics", "params": params}, ambulatorio, user_id)
        
        # ==================== SUSPEND PATIENT ====================
        elif action_type == "suspend_patient":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            previous_status = patient.get("status", "in_cura")
            
            if previous_status == "sospeso":
                return {"success": False, "message": f"⚠️ Il paziente **{patient['cognome']} {patient['nome']}** è già sospeso"}
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "suspend_patient",
                f"Sospeso paziente {patient['cognome']} {patient['nome']}",
                {"patient_id": patient["id"], "previous_status": previous_status}
            )
            
            await db.patients.update_one(
                {"id": patient["id"]},
                {"$set": {"status": "sospeso", "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            
            return {"success": True, 
                    "message": f"✅ Paziente sospeso!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📋 Stato: Sospeso\n\nIl paziente è stato temporaneamente sospeso.\n\n💡 Puoi annullare dicendo 'annulla'",
                    "can_undo": True}
        
        # ==================== RESUME PATIENT ====================
        elif action_type == "resume_patient":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            previous_status = patient.get("status", "sospeso")
            
            if previous_status == "in_cura":
                return {"success": False, "message": f"⚠️ Il paziente **{patient['cognome']} {patient['nome']}** è già in cura"}
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "resume_patient",
                f"Ripreso in cura paziente {patient['cognome']} {patient['nome']}",
                {"patient_id": patient["id"], "previous_status": previous_status}
            )
            
            await db.patients.update_one(
                {"id": patient["id"]},
                {"$set": {"status": "in_cura", "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            
            return {"success": True, 
                    "message": f"✅ Paziente ripreso in cura!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📋 Stato: In cura\n\nIl paziente è stato ripreso in cura.\n\n💡 Puoi annullare dicendo 'annulla'",
                    "can_undo": True}
        
        # ==================== DISCHARGE PATIENT ====================
        elif action_type == "discharge_patient":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            previous_status = patient.get("status", "in_cura")
            previous_data = {"data_dimissione": patient.get("data_dimissione")}
            
            if previous_status == "dimesso":
                return {"success": False, "message": f"⚠️ Il paziente **{patient['cognome']} {patient['nome']}** è già dimesso"}
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "discharge_patient",
                f"Dimesso paziente {patient['cognome']} {patient['nome']}",
                {"patient_id": patient["id"], "previous_status": previous_status, "previous_data": previous_data}
            )
            
            await db.patients.update_one(
                {"id": patient["id"]},
                {"$set": {"status": "dimesso", "data_dimissione": datetime.now().strftime("%Y-%m-%d"), "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            
            return {"success": True, 
                    "message": f"✅ Paziente dimesso!\n\n👤 **{patient['cognome']} {patient['nome']}**\n📋 Stato: Dimesso\n📅 Data dimissione: {datetime.now().strftime('%d/%m/%Y')}\n\nIl paziente è stato dimesso.\n\n💡 Puoi annullare dicendo 'annulla'",
                    "can_undo": True}
        
        # ==================== DELETE PATIENT ====================
        elif action_type == "delete_patient":
            patient_name = params.get("patient_name", "")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            patient_id = patient["id"]
            nome_completo = f"{patient['cognome']} {patient['nome']}"
            
            # Recupera tutti i dati correlati PRIMA di eliminarli (per undo)
            patient_data = {k: v for k, v in patient.items() if k != "_id"}
            appointments = await db.appointments.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
            schede_impianto = await db.schede_impianto_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
            schede_gestione = await db.schede_gestione_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
            schede_med = await db.schede_medicazione_med.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
            prescrizioni_list = await db.prescrizioni.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
            
            # Salva per undo
            await save_undo_action(
                user_id, ambulatorio, "delete_patient",
                f"Eliminato paziente {nome_completo}",
                {
                    "patient_data": patient_data,
                    "appointments": appointments,
                    "schede_impianto": schede_impianto,
                    "schede_gestione": schede_gestione,
                    "schede_med": schede_med,
                    "prescrizioni": prescrizioni_list
                }
            )
            
            # Delete all related data
            await db.appointments.delete_many({"patient_id": patient_id})
            await db.schede_impianto_picc.delete_many({"patient_id": patient_id})
            await db.schede_gestione_picc.delete_many({"patient_id": patient_id})
            await db.schede_medicazione_med.delete_many({"patient_id": patient_id})
            await db.prescrizioni.delete_many({"patient_id": patient_id})
            await db.patients.delete_one({"id": patient_id})
            
            return {"success": True, 
                    "message": f"✅ Paziente eliminato definitivamente!\n\n👤 **{nome_completo}**\n\n⚠️ Tutti i dati del paziente sono stati eliminati.\n\n💡 **IMPORTANTE**: Puoi ancora annullare questa azione dicendo 'annulla'!",
                    "can_undo": True}
        
        # ==================== COMPARE STATISTICS ====================
        elif action_type == "compare_statistics":
            tipo = params.get("tipo", "tutti")
            periodo1 = params.get("periodo1", {})
            periodo2 = params.get("periodo2", {})
            generate_pdf = params.get("generate_pdf", False)
            
            anno1 = periodo1.get("anno", datetime.now().year - 1)
            mese1 = periodo1.get("mese")
            anno2 = periodo2.get("anno", datetime.now().year)
            mese2 = periodo2.get("mese")
            
            async def get_stats_for_period(anno, mese, tipo):
                if mese:
                    start_date = f"{anno}-{mese:02d}-01"
                    end_date = f"{anno}-{mese + 1:02d}-01" if mese < 12 else f"{anno + 1}-01-01"
                else:
                    start_date = f"{anno}-01-01"
                    end_date = f"{anno + 1}-01-01"
                
                query = {
                    "ambulatorio": ambulatorio,
                    "data": {"$gte": start_date, "$lt": end_date},
                    "stato": {"$ne": "non_presentato"}
                }
                if tipo and tipo not in ["tutti", "IMPIANTI"]:
                    query["tipo"] = tipo
                
                appointments = await db.appointments.find(query).to_list(10000)
                
                # Impianti
                imp_query = {"ambulatorio": ambulatorio, "data_impianto": {"$gte": start_date, "$lt": end_date}}
                impianti = await db.schede_impianto_picc.find(imp_query).to_list(10000)
                
                prestazioni_count = {}
                for app in appointments:
                    for prest in app.get("prestazioni", []):
                        prestazioni_count[prest] = prestazioni_count.get(prest, 0) + 1
                
                return {
                    "accessi": len(appointments),
                    "pazienti_unici": len(set(a["patient_id"] for a in appointments)),
                    "prestazioni": prestazioni_count,
                    "impianti": len(impianti)
                }
            
            stats1 = await get_stats_for_period(anno1, mese1, tipo)
            stats2 = await get_stats_for_period(anno2, mese2, tipo)
            
            periodo1_label = f"{MESI[mese1]} {anno1}" if mese1 else f"Anno {anno1}"
            periodo2_label = f"{MESI[mese2]} {anno2}" if mese2 else f"Anno {anno2}"
            
            # Calculate differences
            diff_accessi = stats2["accessi"] - stats1["accessi"]
            diff_pazienti = stats2["pazienti_unici"] - stats1["pazienti_unici"]
            diff_impianti = stats2["impianti"] - stats1["impianti"]
            
            def format_diff(val):
                if val > 0:
                    return f"📈 +{val}"
                elif val < 0:
                    return f"📉 {val}"
                return "➡️ 0"
            
            msg = f"📊 **Confronto Statistiche**\n\n"
            msg += f"**{periodo1_label}** vs **{periodo2_label}**\n\n"
            msg += f"| Metrica | {periodo1_label} | {periodo2_label} | Diff |\n"
            msg += f"|---------|---------|---------|------|\n"
            msg += f"| Accessi | {stats1['accessi']} | {stats2['accessi']} | {format_diff(diff_accessi)} |\n"
            msg += f"| Pazienti | {stats1['pazienti_unici']} | {stats2['pazienti_unici']} | {format_diff(diff_pazienti)} |\n"
            msg += f"| Impianti | {stats1['impianti']} | {stats2['impianti']} | {format_diff(diff_impianti)} |\n"
            
            if generate_pdf:
                msg += "\n\n📥 Clicca 'Scarica PDF' per il report completo."
            
            result = {
                "success": True,
                "message": msg,
                "stats1": stats1,
                "stats2": stats2,
                "periodo1": periodo1_label,
                "periodo2": periodo2_label
            }
            
            if generate_pdf:
                result["pdf_endpoint"] = f"/statistics/compare/pdf?ambulatorio={ambulatorio}&anno1={anno1}&mese1={mese1 or ''}&anno2={anno2}&mese2={mese2 or ''}&tipo={tipo}"
                result["filename"] = f"confronto_{periodo1_label}_{periodo2_label}.pdf"
            
            return result
        
        # ==================== PRINT PATIENT FOLDER ====================
        elif action_type == "print_patient_folder":
            patient_name = params.get("patient_name", "")
            sezione = params.get("sezione", "completa")
            patient = await find_patient(patient_name)
            
            if not patient:
                return {"success": False, "message": f"❌ Paziente '{patient_name}' non trovato"}
            
            sezione_labels = {
                "completa": "Cartella Completa",
                "anagrafica": "Anagrafica",
                "impianto": "Scheda Impianto",
                "gestione_picc": "Gestione PICC",
                "scheda_med": "Scheda Medicazione"
            }
            
            label = sezione_labels.get(sezione, sezione)
            
            return {
                "success": True,
                "message": f"📄 **PDF Pronto!**\n\n👤 **{patient['cognome']} {patient['nome']}**\n📋 Sezione: {label}\n\nClicca 'Scarica PDF' per scaricare.",
                "pdf_endpoint": f"/patients/{patient['id']}/export/pdf?sezione={sezione}",
                "filename": f"{patient['cognome']}_{patient['nome']}_{sezione}.pdf"
            }
        
        # ==================== CREATE MULTIPLE PATIENTS (BATCH) ====================
        elif action_type == "create_multiple_patients":
            patients_data = params.get("patients", [])
            
            if not patients_data:
                return {"success": False, "message": "❌ Nessun paziente da creare. Fornisci una lista di pazienti."}
            
            created = []
            errors = []
            patient_ids = []
            
            for p in patients_data:
                try:
                    patient_data = {
                        "id": str(uuid.uuid4()),
                        "nome": p.get("nome", ""),
                        "cognome": p.get("cognome", ""),
                        "tipo": p.get("tipo", "PICC"),
                        "ambulatorio": ambulatorio,
                        "status": "in_cura",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    await db.patients.insert_one(patient_data)
                    created.append(f"{p.get('cognome', '')} {p.get('nome', '')} ({p.get('tipo', 'PICC')})")
                    patient_ids.append(patient_data["id"])
                except Exception as e:
                    errors.append(f"{p.get('cognome', '')} {p.get('nome', '')}: {str(e)}")
            
            if created:
                # Salva per undo
                await save_undo_action(
                    user_id, ambulatorio, "create_multiple_patients",
                    f"Creati {len(created)} pazienti",
                    {"patient_ids": patient_ids}
                )
            
            msg = f"✅ **Creati {len(created)} pazienti:**\n\n"
            for name in created:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n\n⚠️ **Errori ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n💡 Puoi annullare dicendo 'annulla'"
            
            return {"success": True, "created": len(created), "errors": len(errors), "message": msg, "can_undo": True}
        
        # ==================== SUSPEND MULTIPLE PATIENTS (BATCH) ====================
        elif action_type == "suspend_multiple_patients":
            patient_names = params.get("patient_names", [])
            
            if not patient_names:
                return {"success": False, "message": "❌ Nessun paziente specificato."}
            
            suspended = []
            errors = []
            undo_data = []
            
            for name in patient_names:
                patient = await find_patient(name)
                if not patient:
                    errors.append(f"{name}: non trovato")
                    continue
                
                if patient.get("status") == "sospeso":
                    errors.append(f"{patient['cognome']} {patient['nome']}: già sospeso")
                    continue
                
                previous_status = patient.get("status", "in_cura")
                undo_data.append({"patient_id": patient["id"], "previous_status": previous_status})
                
                await db.patients.update_one(
                    {"id": patient["id"]},
                    {"$set": {"status": "sospeso", "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                suspended.append(f"{patient['cognome']} {patient['nome']}")
            
            if suspended:
                await save_undo_action(
                    user_id, ambulatorio, "suspend_multiple_patients",
                    f"Sospesi {len(suspended)} pazienti",
                    {"patients_data": undo_data}
                )
            
            msg = f"✅ **Sospesi {len(suspended)} pazienti:**\n\n"
            for name in suspended:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n⚠️ **Non processati ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n💡 Puoi annullare dicendo 'annulla'"
            
            return {"success": True, "suspended": len(suspended), "errors": len(errors), "message": msg, "can_undo": True}
        
        # ==================== RESUME MULTIPLE PATIENTS (BATCH) ====================
        elif action_type == "resume_multiple_patients":
            patient_names = params.get("patient_names", [])
            
            if not patient_names:
                return {"success": False, "message": "❌ Nessun paziente specificato."}
            
            resumed = []
            errors = []
            undo_data = []
            
            for name in patient_names:
                patient = await find_patient(name)
                if not patient:
                    errors.append(f"{name}: non trovato")
                    continue
                
                if patient.get("status") == "in_cura":
                    errors.append(f"{patient['cognome']} {patient['nome']}: già in cura")
                    continue
                
                previous_status = patient.get("status", "sospeso")
                undo_data.append({"patient_id": patient["id"], "previous_status": previous_status})
                
                await db.patients.update_one(
                    {"id": patient["id"]},
                    {"$set": {"status": "in_cura", "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                resumed.append(f"{patient['cognome']} {patient['nome']}")
            
            if resumed:
                await save_undo_action(
                    user_id, ambulatorio, "resume_multiple_patients",
                    f"Ripresi in cura {len(resumed)} pazienti",
                    {"patients_data": undo_data}
                )
            
            msg = f"✅ **Ripresi in cura {len(resumed)} pazienti:**\n\n"
            for name in resumed:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n⚠️ **Non processati ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n💡 Puoi annullare dicendo 'annulla'"
            
            return {"success": True, "resumed": len(resumed), "errors": len(errors), "message": msg, "can_undo": True}
        
        # ==================== DISCHARGE MULTIPLE PATIENTS (BATCH) ====================
        elif action_type == "discharge_multiple_patients":
            patient_names = params.get("patient_names", [])
            
            if not patient_names:
                return {"success": False, "message": "❌ Nessun paziente specificato."}
            
            discharged = []
            errors = []
            undo_data = []
            
            for name in patient_names:
                patient = await find_patient(name)
                if not patient:
                    errors.append(f"{name}: non trovato")
                    continue
                
                if patient.get("status") == "dimesso":
                    errors.append(f"{patient['cognome']} {patient['nome']}: già dimesso")
                    continue
                
                previous_status = patient.get("status", "in_cura")
                previous_data = {"data_dimissione": patient.get("data_dimissione")}
                undo_data.append({"patient_id": patient["id"], "previous_status": previous_status, "previous_data": previous_data})
                
                await db.patients.update_one(
                    {"id": patient["id"]},
                    {"$set": {"status": "dimesso", "data_dimissione": datetime.now().strftime("%Y-%m-%d"), "updated_at": datetime.now(timezone.utc).isoformat()}}
                )
                discharged.append(f"{patient['cognome']} {patient['nome']}")
            
            if discharged:
                await save_undo_action(
                    user_id, ambulatorio, "discharge_multiple_patients",
                    f"Dimessi {len(discharged)} pazienti",
                    {"patients_data": undo_data}
                )
            
            msg = f"✅ **Dimessi {len(discharged)} pazienti:**\n\n"
            for name in discharged:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n⚠️ **Non processati ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n💡 Puoi annullare dicendo 'annulla'"
            
            return {"success": True, "discharged": len(discharged), "errors": len(errors), "message": msg, "can_undo": True}
        
        # ==================== DELETE MULTIPLE PATIENTS (BATCH) ====================
        elif action_type == "delete_multiple_patients":
            patient_names = params.get("patient_names", [])
            
            if not patient_names:
                return {"success": False, "message": "❌ Nessun paziente specificato."}
            
            deleted = []
            errors = []
            all_backup_data = []
            
            for name in patient_names:
                patient = await find_patient(name)
                if not patient:
                    errors.append(f"{name}: non trovato")
                    continue
                
                patient_id = patient["id"]
                nome_completo = f"{patient['cognome']} {patient['nome']}"
                
                # Backup data for undo
                patient_data = {k: v for k, v in patient.items() if k != "_id"}
                appointments = await db.appointments.find({"patient_id": patient_id}, {"_id": 0}).to_list(1000)
                schede_impianto = await db.schede_impianto_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
                schede_gestione = await db.schede_gestione_picc.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
                schede_med = await db.schede_medicazione_med.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
                prescrizioni_list = await db.prescrizioni.find({"patient_id": patient_id}, {"_id": 0}).to_list(100)
                
                all_backup_data.append({
                    "patient_data": patient_data,
                    "appointments": appointments,
                    "schede_impianto": schede_impianto,
                    "schede_gestione": schede_gestione,
                    "schede_med": schede_med,
                    "prescrizioni": prescrizioni_list
                })
                
                # Delete all related data
                await db.appointments.delete_many({"patient_id": patient_id})
                await db.schede_impianto_picc.delete_many({"patient_id": patient_id})
                await db.schede_gestione_picc.delete_many({"patient_id": patient_id})
                await db.schede_medicazione_med.delete_many({"patient_id": patient_id})
                await db.prescrizioni.delete_many({"patient_id": patient_id})
                await db.patients.delete_one({"id": patient_id})
                
                deleted.append(nome_completo)
            
            if deleted:
                await save_undo_action(
                    user_id, ambulatorio, "delete_multiple_patients",
                    f"Eliminati {len(deleted)} pazienti",
                    {"all_backup_data": all_backup_data}
                )
            
            msg = f"✅ **Eliminati definitivamente {len(deleted)} pazienti:**\n\n"
            for name in deleted:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n⚠️ **Non trovati ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n\n⚠️ Tutti i dati dei pazienti sono stati eliminati.\n💡 **IMPORTANTE**: Puoi ancora annullare questa azione dicendo 'annulla'!"
            
            return {"success": True, "deleted": len(deleted), "errors": len(errors), "message": msg, "can_undo": True}
        
        # ==================== ADD EXTRACTED PATIENTS (from image) ====================
        elif action_type == "add_extracted_patients":
            patients_data = params.get("patients", [])
            tipo_default = params.get("tipo_default", "PICC")
            
            if not patients_data:
                return {"success": False, "message": "❌ Nessun paziente da aggiungere. Prima carica una foto con i nomi dei pazienti."}
            
            created = []
            errors = []
            patient_ids = []
            
            for p in patients_data:
                try:
                    patient_data = {
                        "id": str(uuid.uuid4()),
                        "nome": p.get("nome", ""),
                        "cognome": p.get("cognome", ""),
                        "tipo": p.get("tipo", tipo_default),
                        "ambulatorio": ambulatorio,
                        "status": "in_cura",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                    await db.patients.insert_one(patient_data)
                    created.append(f"{p.get('cognome', '')} {p.get('nome', '')} ({p.get('tipo', tipo_default)})")
                    patient_ids.append(patient_data["id"])
                except Exception as e:
                    errors.append(f"{p.get('cognome', '')} {p.get('nome', '')}: {str(e)}")
            
            if created:
                await save_undo_action(
                    user_id, ambulatorio, "create_multiple_patients",
                    f"Creati {len(created)} pazienti da foto",
                    {"patient_ids": patient_ids}
                )
            
            msg = f"✅ **Creati {len(created)} pazienti dalla foto:**\n\n"
            for name in created:
                msg += f"• {name}\n"
            
            if errors:
                msg += f"\n\n⚠️ **Errori ({len(errors)}):**\n"
                for err in errors:
                    msg += f"• {err}\n"
            
            msg += "\n💡 Puoi annullare dicendo 'annulla'"
            
            return {"success": True, "created": len(created), "errors": len(errors), "message": msg, "can_undo": True}
        
        return {"success": False, "message": "❌ Azione non riconosciuta. Prova a riformulare la richiesta."}
        
    except Exception as e:
        logger.error(f"Action error: {str(e)}")
        return {"success": False, "message": f"❌ Errore nell'esecuzione: {str(e)}"}

@api_router.post("/ai/chat")
async def ai_chat(
    request: AIChatRequest,
    payload: dict = Depends(verify_token)
):
    """Chat with AI assistant"""
    if request.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    user_id = payload.get("sub", "unknown")
    session_id = request.session_id or str(uuid.uuid4())
    
    # Save user message
    user_msg = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "user_id": user_id,
        "ambulatorio": request.ambulatorio.value,
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await db.ai_chat_history.insert_one(user_msg)
    
    # Get AI response
    ai_result = await get_ai_response(request.message, session_id, request.ambulatorio.value, user_id)
    
    # Execute action if present
    action_result = None
    if ai_result.get("action"):
        action_result = await execute_ai_action(ai_result["action"], request.ambulatorio.value, user_id)
        # Update response with action result
        if action_result.get("success"):
            ai_result["response"] = action_result.get("message", ai_result["response"])
        elif action_result.get("message"):
            ai_result["response"] = action_result["message"]
    
    # Save assistant message
    assistant_msg = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "user_id": user_id,
        "ambulatorio": request.ambulatorio.value,
        "role": "assistant",
        "content": ai_result["response"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    await db.ai_chat_history.insert_one(assistant_msg)
    
    return {
        "response": ai_result["response"],
        "session_id": session_id,
        "action_performed": action_result
    }

# Import for image processing - use OpenAI directly for vision
import openai

@api_router.post("/ai/extract-from-image")
async def extract_patients_from_image(
    ambulatorio: str = Form(...),
    tipo_default: str = Form("PICC"),
    file: UploadFile = File(...),
    payload: dict = Depends(verify_token)
):
    """Extract patient names from an uploaded image using AI vision"""
    if ambulatorio not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    try:
        api_key = os.environ.get('EMERGENT_LLM_KEY')
        if not api_key:
            raise HTTPException(status_code=500, detail="Chiave API non configurata")
        
        # Read and encode image
        contents = await file.read()
        image_base64 = base64.b64encode(contents).decode('utf-8')
        
        # Determine content type
        content_type = file.content_type or "image/png"
        
        # Use OpenAI directly with vision capability
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://integrations.emergentagent.com/llm/openai/v1"
        )
        
        # Create the message with image
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": """Sei un assistente che estrae nomi di pazienti da immagini di liste o elenchi.
Analizza l'immagine e estrai TUTTI i nomi di persone che vedi nell'elenco.
Restituisci SOLO un JSON valido nel formato:
{
    "patients": [
        {"cognome": "Rossi", "nome": "Mario"},
        {"cognome": "Bianchi", "nome": "Luigi"}
    ]
}
IMPORTANTE: 
- Estrai TUTTI i nomi visibili nell'immagine
- Il cognome va prima del nome
- Non includere altro testo, solo il JSON
Se non riesci a identificare nomi, restituisci: {"patients": [], "error": "Nessun nome identificato"}"""
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Estrai tutti i nomi di persone (cognome e nome) da questa immagine. Restituisci solo il JSON con la lista completa dei pazienti."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=4096
        )
        
        response_text = response.choices[0].message.content
        logger.info(f"AI Vision response: {response_text[:500]}")
        
        # Parse response
        try:
            # Try to find JSON in the response
            # Handle markdown code blocks
            if "```json" in response_text:
                json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    result = json.loads(response_text.strip())
            elif "```" in response_text:
                json_match = re.search(r'```\s*(.*?)\s*```', response_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    result = json.loads(response_text.strip())
            else:
                # Try to find JSON object directly
                json_match = re.search(r'\{.*"patients".*\}', response_text, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    result = json.loads(response_text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}, response: {response_text}")
            result = {"patients": [], "raw_response": response_text, "error": str(e)}
        
        patients = result.get("patients", [])
        
        # Add default type to each patient
        for p in patients:
            if "tipo" not in p:
                p["tipo"] = tipo_default
        
        return {
            "success": True,
            "patients": patients,
            "count": len(patients),
            "tipo_default": tipo_default,
            "message": f"Estratti {len(patients)} pazienti dall'immagine"
        }
        
    except Exception as e:
        logger.error(f"Image extraction error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Errore nell'estrazione: {str(e)}")

@api_router.get("/ai/history")
async def get_ai_history(
    ambulatorio: Ambulatorio,
    session_id: Optional[str] = None,
    payload: dict = Depends(verify_token)
):
    """Get chat history"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    user_id = payload.get("sub", "unknown")
    
    query = {"user_id": user_id, "ambulatorio": ambulatorio.value}
    if session_id:
        query["session_id"] = session_id
    
    messages = await db.ai_chat_history.find(query, {"_id": 0}).sort("timestamp", -1).to_list(100)
    
    # Group by session
    sessions = {}
    for msg in messages:
        sid = msg["session_id"]
        if sid not in sessions:
            sessions[sid] = {"session_id": sid, "messages": [], "last_message": msg["timestamp"]}
        sessions[sid]["messages"].append(msg)
    
    return list(sessions.values())

@api_router.get("/ai/sessions")
async def get_ai_sessions(
    ambulatorio: Ambulatorio,
    payload: dict = Depends(verify_token)
):
    """Get all chat sessions"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    user_id = payload.get("sub", "unknown")
    
    pipeline = [
        {"$match": {"user_id": user_id, "ambulatorio": ambulatorio.value}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$session_id",
            "last_message": {"$first": "$content"},
            "last_timestamp": {"$first": "$timestamp"},
            "message_count": {"$sum": 1}
        }},
        {"$sort": {"last_timestamp": -1}},
        {"$limit": 20}
    ]
    
    sessions = await db.ai_chat_history.aggregate(pipeline).to_list(20)
    return [{"session_id": s["_id"], "last_message": s["last_message"][:50] + "..." if len(s["last_message"]) > 50 else s["last_message"], "last_timestamp": s["last_timestamp"], "message_count": s["message_count"]} for s in sessions]

@api_router.delete("/ai/session/{session_id}")
async def delete_ai_session(
    session_id: str,
    ambulatorio: Ambulatorio,
    payload: dict = Depends(verify_token)
):
    """Delete a chat session"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    user_id = payload.get("sub", "unknown")
    
    result = await db.ai_chat_history.delete_many({
        "session_id": session_id,
        "user_id": user_id,
        "ambulatorio": ambulatorio.value
    })
    
    return {"deleted": result.deleted_count}

@api_router.delete("/ai/history")
async def clear_ai_history(
    ambulatorio: Ambulatorio,
    payload: dict = Depends(verify_token)
):
    """Clear all chat history"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    user_id = payload.get("sub", "unknown")
    
    result = await db.ai_chat_history.delete_many({
        "user_id": user_id,
        "ambulatorio": ambulatorio.value
    })
    
    return {"deleted": result.deleted_count}

# ============== GOOGLE SHEETS SYNC ==============
import csv
import httpx
import re as regex_module

GOOGLE_SHEET_ID = "1gO9i0IuoReM0yto7GqQlIMWjdrzDToDWJ9dQ8z0badE"

class GoogleSheetsSyncRequest(BaseModel):
    ambulatorio: Ambulatorio
    sheet_id: Optional[str] = None
    start_date: Optional[str] = None  # Data del lunedì da cui partire (YYYY-MM-DD), default: lunedì settimana corrente

@api_router.post("/sync/google-sheets")
async def sync_from_google_sheets(
    data: GoogleSheetsSyncRequest,
    payload: dict = Depends(verify_token)
):
    """Sincronizza appuntamenti da Google Sheets"""
    if data.ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    sheet_id = data.sheet_id or GOOGLE_SHEET_ID
    
    try:
        # Scarica il foglio come CSV
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        
        async with httpx.AsyncClient(follow_redirects=True) as http_client:
            response = await http_client.get(csv_url, timeout=30.0)
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Impossibile accedere al foglio Google (status {response.status_code}). Verifica che sia pubblico.")
        
        # Parse CSV
        csv_content = response.text
        lines = list(csv.reader(io.StringIO(csv_content)))
        
        # Struttura del foglio:
        # Riga 5 (indice 4): giorni della settimana (lunedi, martedi, etc.)
        # Riga 6 (indice 5): tipi PICC/MEDICAZIONI
        # Righe 7+ (indice 6+): orari (col 1) e nomi pazienti
        
        weekdays_row = lines[4] if len(lines) > 4 else []  # Riga 5 = indice 4
        types_row = lines[5] if len(lines) > 5 else []  # Riga 6 = indice 5
        
        # Calcola le date della settimana corrente
        today = datetime.now().date()
        # Trova il lunedì della settimana corrente
        monday_this_week = today - timedelta(days=today.weekday())
        
        # Mappa giorni italiani a offset dal lunedì
        day_name_to_offset = {
            "lunedi": 0, "lunedì": 0,
            "martedi": 1, "martedì": 1,
            "mercoledi": 2, "mercoledì": 2,
            "giovedi": 3, "giovedì": 3,
            "venerdi": 4, "venerdì": 4,
            "sabato": 5,
            "domenica": 6
        }
        
        # Mappa colonne a date e tipi
        column_mapping = {}  # {col_index: {"date": "2026-01-12", "tipo": "PICC"}}
        
        # Trova i giorni della settimana e calcola le date corrispondenti
        day_for_col = {}
        current_day_offset = None
        
        for col_idx, cell in enumerate(weekdays_row):
            cell_clean = cell.strip().lower().replace(" ", "")
            if cell_clean in day_name_to_offset:
                current_day_offset = day_name_to_offset[cell_clean]
            if current_day_offset is not None:
                day_for_col[col_idx] = current_day_offset
        
        # Calcola le date effettive per ogni colonna
        date_for_col = {}
        for col_idx, day_offset in day_for_col.items():
            target_date = monday_this_week + timedelta(days=day_offset)
            date_for_col[col_idx] = target_date.strftime("%Y-%m-%d")
        
        logger.info(f"Date mapping: {date_for_col}")
        
        # Ora associa le colonne ai tipi PICC/MED
        # La data si propaga alle colonne successive fino alla prossima data
        date_boundaries = sorted(date_for_col.keys())
        
        for col_idx, cell in enumerate(types_row):
            tipo_cell = cell.strip().upper()
            if not tipo_cell:
                continue
                
            # Trova la data più vicina a sinistra
            closest_date = None
            for boundary in reversed(date_boundaries):
                if boundary <= col_idx:
                    closest_date = date_for_col[boundary]
                    break
            
            if closest_date:
                if "PICC" in tipo_cell and "MED" not in tipo_cell:
                    column_mapping[col_idx] = {"date": closest_date, "tipo": "PICC"}
                elif "MED" in tipo_cell:
                    column_mapping[col_idx] = {"date": closest_date, "tipo": "MED"}
        
        logger.info(f"Column mapping: {column_mapping}")
        
        # Parse appuntamenti dalle righe successive (dalla riga 7 = indice 6)
        appointments_to_create = []
        patients_to_create = set()  # Set di (cognome, nome) per evitare duplicati
        
        for row_idx, row in enumerate(lines[6:], start=6):  # Inizia dalla riga 7
            # Trova l'orario nella colonna B (indice 1)
            ora = None
            ora_cell = row[1].strip() if len(row) > 1 else ""
            if ora_cell and ":" in ora_cell:
                # Verifica formato orario HH:MM
                if regex_module.match(r'^\d{1,2}:\d{2}$', ora_cell):
                    ora = ora_cell if len(ora_cell) == 5 else f"0{ora_cell}"
            
            if not ora:
                continue
            
            # Scansiona le colonne mappate
            for col_idx, mapping in column_mapping.items():
                if col_idx < len(row):
                    cell = row[col_idx].strip()
                    if cell and cell not in ["", "-"]:
                        # Può contenere più nomi separati da / o ,
                        names = regex_module.split(r'[/,]', cell)
                        for name in names:
                            name = name.strip()
                            if name and len(name) > 1:
                                # Ignora note come "rim picc", "controllo", etc.
                                if any(kw in name.lower() for kw in ["controllo", "rim ", "non funzionante", "picc port", "idline", "clody im", "spatoliatore"]):
                                    continue
                                
                                # Estrai cognome (primo elemento)
                                parts = name.split()
                                if parts:
                                    cognome = parts[0].capitalize()
                                    nome = " ".join(parts[1:]).capitalize() if len(parts) > 1 else ""
                                    
                                    patients_to_create.add((cognome, nome))
                                    appointments_to_create.append({
                                        "date": mapping["date"],
                                        "ora": ora,
                                        "tipo": mapping["tipo"],
                                        "cognome": cognome,
                                        "nome": nome
                                    })
        
        # Crea/trova pazienti e appuntamenti
        created_patients = 0
        created_appointments = 0
        skipped_appointments = 0
        
        patient_id_map = {}  # {(cognome, nome): patient_id}
        
        for cognome, nome in patients_to_create:
            # Cerca paziente esistente
            query = {"cognome": {"$regex": f"^{cognome}$", "$options": "i"}, "ambulatorio": data.ambulatorio.value}
            if nome:
                query["nome"] = {"$regex": f"^{nome}$", "$options": "i"}
            
            existing = await db.patients.find_one(query, {"_id": 0})
            
            if existing:
                patient_id_map[(cognome, nome)] = existing["id"]
            else:
                # Crea nuovo paziente
                new_patient_id = str(uuid.uuid4())
                codice_paziente = generate_patient_code(nome or "X", cognome)
                while await db.patients.find_one({"codice_paziente": codice_paziente}):
                    codice_paziente = generate_patient_code(nome or "X", cognome)
                
                # Determina tipo paziente basato sugli appuntamenti
                patient_tipos = set()
                for apt in appointments_to_create:
                    if apt["cognome"] == cognome and apt["nome"] == nome:
                        patient_tipos.add(apt["tipo"])
                
                if "PICC" in patient_tipos and "MED" in patient_tipos:
                    patient_tipo = "PICC_MED"
                elif "PICC" in patient_tipos:
                    patient_tipo = "PICC"
                else:
                    patient_tipo = "MED"
                
                new_patient = {
                    "id": new_patient_id,
                    "codice_paziente": codice_paziente,
                    "nome": nome or "",
                    "cognome": cognome,
                    "tipo": patient_tipo,
                    "ambulatorio": data.ambulatorio.value,
                    "status": "in_cura",
                    "scheda_med_counter": 0,
                    "lesion_markers": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }
                await db.patients.insert_one(new_patient)
                patient_id_map[(cognome, nome)] = new_patient_id
                created_patients += 1
        
        # Crea appuntamenti
        for apt in appointments_to_create:
            patient_id = patient_id_map.get((apt["cognome"], apt["nome"]))
            if not patient_id:
                continue
            
            # Verifica se esiste già un appuntamento per questo paziente in questo slot
            existing_apt = await db.appointments.find_one({
                "patient_id": patient_id,
                "data": apt["date"],
                "ora": apt["ora"],
                "ambulatorio": data.ambulatorio.value
            })
            
            if existing_apt:
                skipped_appointments += 1
                continue
            
            # Crea appuntamento
            new_apt = {
                "id": str(uuid.uuid4()),
                "patient_id": patient_id,
                "patient_nome": apt["nome"],
                "patient_cognome": apt["cognome"],
                "ambulatorio": data.ambulatorio.value,
                "data": apt["date"],
                "ora": apt["ora"],
                "tipo": apt["tipo"],
                "prestazioni": ["medicazione_semplice"] if apt["tipo"] == "MED" else ["medicazione_semplice", "irrigazione_catetere"],
                "note": "Importato da Google Sheets",
                "stato": "da_fare",
                "completed": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            await db.appointments.insert_one(new_apt)
            created_appointments += 1
        
        return {
            "success": True,
            "message": f"Sincronizzazione completata",
            "created_patients": created_patients,
            "created_appointments": created_appointments,
            "skipped_appointments": skipped_appointments,
            "total_parsed": len(appointments_to_create)
        }
        
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout nella connessione a Google Sheets")
    except Exception as e:
        logger.error(f"Google Sheets sync error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Errore nella sincronizzazione: {str(e)}")

@api_router.get("/sync/google-sheets/preview")
async def preview_google_sheets_sync(
    ambulatorio: Ambulatorio,
    sheet_id: Optional[str] = None,
    year: Optional[int] = None,
    payload: dict = Depends(verify_token)
):
    """Anteprima dei dati da Google Sheets senza salvarli"""
    if ambulatorio.value not in payload["ambulatori"]:
        raise HTTPException(status_code=403, detail="Non hai accesso a questo ambulatorio")
    
    sheet_id = sheet_id or GOOGLE_SHEET_ID
    year = year or datetime.now().year
    
    try:
        csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        
        async with httpx.AsyncClient(follow_redirects=True) as http_client:
            response = await http_client.get(csv_url, timeout=30.0)
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail="Impossibile accedere al foglio Google")
        
        csv_content = response.text
        lines = list(csv.reader(io.StringIO(csv_content)))
        
        # Estrai date dalla riga 3
        dates_row = lines[2] if len(lines) > 2 else []
        dates_found = []
        for cell in dates_row:
            cell = cell.strip()
            if cell and "/" in cell:
                try:
                    parts = cell.split("/")
                    day = int(parts[0])
                    month = int(parts[1])
                    dates_found.append(f"{year}-{month:02d}-{day:02d}")
                except:
                    pass
        
        # Conta righe con dati
        data_rows = 0
        for row in lines[4:]:
            if any(cell.strip() for cell in row):
                data_rows += 1
        
        return {
            "success": True,
            "sheet_id": sheet_id,
            "dates_found": list(set(dates_found)),
            "data_rows": data_rows,
            "preview": lines[:10]  # Prime 10 righe come anteprima
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore: {str(e)}")

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
