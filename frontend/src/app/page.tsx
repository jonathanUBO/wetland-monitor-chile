'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import Map, { Source, Layer } from 'react-map-gl';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import {
    Calendar,
    Layers,
    Activity,
    Search,
    Droplets,
    Wind,
    AlertCircle,
    Settings,
    Maximize2,
    ShieldCheck,
    CheckCircle2,
    LogOut,
    Key,
    User,
    X,
    FileText
} from 'lucide-react';
import {
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ResponsiveContainer,
    AreaChart,
    Area,
    Line,
    ReferenceDot
} from 'recharts';
import axios from 'axios';

import {
    getApiUrl,
    resolveBackendUrl,
    CHILEAN_REGIONS,
    ConsolidatedStatus,
    ModeResult,
    GlassPanel,
    getOptimalZoom,
    getBoundsFromGeometry,
    resampleToMonthly,
    METRIC_NAMES,
    mergeTimeSeries,
    LEGENDS,
} from '../lib/wetland';
import { IndexCard } from '../components/IndexCard';


export default function Dashboard() {
    // --- STATE ---
    const [loading, setLoading] = useState(false);
    const [generatingReport, setGeneratingReport] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [sarLoading, setSarLoading] = useState(false);
    const [sarData, setSarData] = useState<any | null>(null);
    const [sarError, setSarError] = useState<string | null>(null);

    // Auth (Simplified for CDSE backend)
    const [isApiReady, setIsApiReady] = useState(true);

    // Analysis
    const [startDate, setStartDate] = useState('2016-03-01');
    const [endDate, setEndDate] = useState('2026-01-22');
    const [results, setResults] = useState<Record<string, ModeResult> | null>(null);
    const [mergedData, setMergedData] = useState<any[]>([]);
    const [processLog, setProcessLog] = useState<string[]>([]);
    const [showDiagnosisModal, setShowDiagnosisModal] = useState(false);

    // Consolidated Ecological Condition (WECI)
    const consolidatedStatus: ConsolidatedStatus | null = React.useMemo(() => {
        if (!results) return null;
        if ((results as any).consolidated_status) return (results as any).consolidated_status;
        if ((results as any)._consolidated) return (results as any)._consolidated;
        return null;
    }, [results]);

    // Global View State (Start vs End Year)
    const [viewYear, setViewYear] = useState<'start' | 'end'>('end');

    // Basemap Mode: Satellite (ESRI) vs Dark
    const [mapMode, setMapMode] = useState<'satellite' | 'dark'>('satellite');

    // Shared Map State (Center)
    const [viewState, setViewState] = useState({
        longitude: -70.9,
        latitude: -33.5,
        zoom: 8
    });

    // Wetland Data
    const [wetlands, setWetlands] = useState<any[]>([]);
    const [filteredWetlands, setFilteredWetlands] = useState<any[]>([]);
    const [searchQuery, setSearchQuery] = useState('');
    const deferredSearchQuery = React.useDeferredValue(searchQuery);
    const [selectedRegion, setSelectedRegion] = useState<string>('');
    const [selectedWetland, setSelectedWetland] = useState<any | null>(null);
    const [showSuggestions, setShowSuggestions] = useState(false);

    // Instant local memory cache for analysis results (<10ms reload)
    const localAnalysisCache = useRef<Record<string, any>>({});
    const analysisAbortController = useRef<AbortController | null>(null);

    // CDSE Credentials - Supports Any Account (Email/Password or OAuth2 Client ID/Secret)
    const [cdseToken, setCdseToken] = useState<string>('');
    const [cdseAuthMode, setCdseAuthMode] = useState<'password' | 'oauth'>('password');
    const [cdseUsername, setCdseUsername] = useState(() => {
        if (typeof window !== 'undefined') {
            try {
                const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                if (saved) return JSON.parse(saved).username || '';
            } catch {}
        }
        return '';
    });
    const [cdsePassword, setCdsePassword] = useState(() => {
        if (typeof window !== 'undefined') {
            try {
                const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                if (saved) return JSON.parse(saved).password || '';
            } catch {}
        }
        return '';
    });
    const [cdseClientId, setCdseClientId] = useState(() => {
        if (typeof window !== 'undefined') {
            try {
                const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                if (saved) return JSON.parse(saved).clientId || '';
            } catch {}
        }
        return '';
    });
    const [cdseClientSecret, setCdseClientSecret] = useState(() => {
        if (typeof window !== 'undefined') {
            try {
                const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                if (saved) return JSON.parse(saved).clientSecret || '';
            } catch {}
        }
        return '';
    });
    const [cdseConfigured, setCdseConfigured] = useState(false);
    const [isInstitutionalServer, setIsInstitutionalServer] = useState(false);
    const [cdseError, setCdseError] = useState<string | null>(null);
    const [cdseSuccess, setCdseSuccess] = useState<string | null>(null);
    const [showCredentials, setShowCredentials] = useState(false);
    const [savingCreds, setSavingCreds] = useState(false);
    const [verifyingCreds, setVerifyingCreds] = useState(false);

    // Custom Area
    const [customArea, setCustomArea] = useState<any | null>(null);
    const [customAreaName, setCustomAreaName] = useState<string>('');
    const [uploadingFile, setUploadingFile] = useState(false);
    const [fileError, setFileError] = useState<string | null>(null);
    const [areaSource, setAreaSource] = useState<'wetland' | 'custom'>('wetland');

    // Search dropdown portal
    const searchInputRef = useRef<HTMLInputElement>(null);
    const [dropdownRect, setDropdownRect] = useState<DOMRect | null>(null);

    const updateDropdownRect = useCallback(() => {
        if (searchInputRef.current) {
            setDropdownRect(searchInputRef.current.getBoundingClientRect());
        }
    }, []);

    const handleMapMove = useCallback((evt: any) => {
        setViewState(evt.viewState);
    }, []);

    // --- EFFECTS ---
    useEffect(() => {
        // Restore local analysis cache from sessionStorage for instantaneous (<10ms) renders
        if (typeof window !== 'undefined') {
            try {
                const cached = sessionStorage.getItem('wetland_v2_analysis_cache');
                if (cached) {
                    localAnalysisCache.current = JSON.parse(cached);
                }
            } catch {}
        }

        const API = getApiUrl();
        // Load lightweight wetlands metadata (~150KB vs 23MB); polygon geometry is fetched on-demand
        fetch(`${API}/api/wetlands`)
            .then(res => res.json())
            .then(data => {
                if (Array.isArray(data)) {
                    setWetlands(data);
                }
            })
            .catch(console.error);

        // Check CDSE credentials status (Dual Mode: Institutional vs BYOC)
        axios.get(`${API}/credentials-status`)
            .then(async r => {
                if (r.data.server_configured) {
                    setCdseConfigured(true);
                    setIsInstitutionalServer(true);
                    setProcessLog(prev => [...prev, '✓  Servidor en Modo Institucional (Credenciales activas)']);
                } else if (r.data.has_active_session) {
                    setCdseConfigured(true);
                    setIsInstitutionalServer(false);
                    setProcessLog(prev => [...prev, '✓  Sesión de usuario activa en el servidor']);
                } else {
                    setIsInstitutionalServer(false);
                }

                // Try auto-restoring credentials from sessionStorage (migrating any legacy localStorage entry)
                try {
                    const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                    if (saved) {
                        sessionStorage.setItem('cdse_credentials', saved);
                        localStorage.removeItem('cdse_credentials');
                        const parsed = JSON.parse(saved);
                        if (parsed.authMode) setCdseAuthMode(parsed.authMode);
                        if (parsed.username) setCdseUsername(parsed.username);
                        if (parsed.password) setCdsePassword(parsed.password);
                        if (parsed.clientId) setCdseClientId(parsed.clientId);
                        if (parsed.clientSecret) setCdseClientSecret(parsed.clientSecret);
                        const hasUserCreds = (parsed.username && parsed.password) || (parsed.clientId && parsed.clientSecret);
                        if (hasUserCreds) {
                            setCdseConfigured(true);
                            setProcessLog(prev => [...prev, '✓  Credenciales CDSE activas en la sesión']);
                        }
                    }
                } catch {
                    // ignore auto-restore error
                }
            })
            .catch(() => {
                // Fallback to sessionStorage credentials if backend is offline/sleeping
                try {
                    const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                    if (saved) {
                        sessionStorage.setItem('cdse_credentials', saved);
                        localStorage.removeItem('cdse_credentials');
                        const parsed = JSON.parse(saved);
                        if (parsed.authMode) setCdseAuthMode(parsed.authMode);
                        if (parsed.username) setCdseUsername(parsed.username);
                        if (parsed.password) setCdsePassword(parsed.password);
                        if (parsed.clientId) setCdseClientId(parsed.clientId);
                        if (parsed.clientSecret) setCdseClientSecret(parsed.clientSecret);
                        const hasUserCreds = (parsed.username && parsed.password) || (parsed.clientId && parsed.clientSecret);
                        if (hasUserCreds) {
                            setCdseConfigured(true);
                        }
                    }
                } catch {}
            });
    }, []);

    // Helper to normalize region names from dataset if encoding characters are present
    const getCleanRegion = useCallback((r: string) => {
        if (!r) return 'Chile';
        if (r.includes('Araucan')) return 'La Araucanía';
        if (r.includes('Biob')) return 'Biobío';
        if (r.includes('R') && r.includes('os')) return 'Los Ríos';
        if (r.includes('Ays')) return 'Aysén';
        if (r.includes('Tarapac')) return 'Tarapacá';
        if (r.includes('Valpara')) return 'Valparaíso';
        if (r.includes('uble') || r.includes('Ñuble')) return 'Ñuble';
        return r;
    }, []);

    // Memoize counts per region
    const regionCounts = React.useMemo(() => {
        const counts: Record<string, number> = {};
        wetlands.forEach(w => {
            const r = getCleanRegion(w.region);
            counts[r] = (counts[r] || 0) + 1;
        });
        return counts;
    }, [wetlands, getCleanRegion]);

    // Filter wetlands by search and region (debounced/deferred for 60fps instant UI)
    useEffect(() => {
        let results = wetlands;
        if (selectedRegion) {
            results = results.filter(w => getCleanRegion(w.region) === selectedRegion);
        }
        if (deferredSearchQuery.trim().length > 0) {
            const query = deferredSearchQuery.toLowerCase().trim();
            results = results.filter(w =>
                w.name.toLowerCase().includes(query) ||
                (w.code && String(w.code).toLowerCase().includes(query)) ||
                (w.region && getCleanRegion(w.region).toLowerCase().includes(query))
            );
        }
        // Limit results to 60 items for instantaneous DOM rendering (<5ms)
        setFilteredWetlands(results.slice(0, 60));
    }, [deferredSearchQuery, selectedRegion, wetlands, getCleanRegion]);

    // Group filtered results by Chilean region
    const groupedWetlands = React.useMemo(() => {
        const groups: Record<string, any[]> = {};
        filteredWetlands.forEach(w => {
            const reg = getCleanRegion(w.region);
            if (!groups[reg]) groups[reg] = [];
            groups[reg].push(w);
        });
        return groups;
    }, [filteredWetlands, getCleanRegion]);

    // --- ACTIONS ---
    const selectWetland = (wetland: any) => {
        setSelectedWetland(wetland);
        setSearchQuery(wetland.name);
        setShowSuggestions(false); // Auto-close suggestions

        if (!wetland.geometry) {
            const key = wetland.name || wetland.code;
            if (key) {
                axios.get(`${getApiUrl()}/api/wetland/${encodeURIComponent(key)}/geometry`)
                    .then(res => {
                        const geom = res.data?.geometry;
                        if (geom) {
                            wetland.geometry = geom;
                            setSelectedWetland((prev: any) =>
                                prev && (prev.id === wetland.id || prev.name === wetland.name)
                                    ? { ...prev, geometry: geom }
                                    : prev
                            );
                        }
                    })
                    .catch(() => {});
            }
        }

        if (wetland.bbox) {
            const [minX, minY, maxX, maxY] = wetland.bbox;
            const optimalZoom = getOptimalZoom([minX, minY, maxX, maxY]);
            setViewState({
                longitude: (minX + maxX) / 2,
                latitude: (minY + maxY) / 2,
                zoom: optimalZoom
            });
        }

        // Instant reload: si este humedal ya tiene análisis para este rango, cargarlo al instante (<10ms)
        const cacheKey = `${wetland.name}_${startDate}_${endDate}`;
        if (localAnalysisCache.current[cacheKey]) {
            const cachedData = localAnalysisCache.current[cacheKey];
            setResults(cachedData);
            setMergedData(resampleToMonthly(mergeTimeSeries(cachedData)));
            setProcessLog(prev => [
                ...prev,
                `⚡ Datos satelitales cargados al instante (<10ms) para ${wetland.name}`
            ]);
        }
    };

    const handleDisconnect = async () => {
        try {
            sessionStorage.removeItem('cdse_credentials');
            localStorage.removeItem('cdse_credentials');
            const API = getApiUrl();
            await axios.post(`${API}/clear-credentials`, {
                username: cdseUsername,
                password: cdsePassword,
                client_id: cdseClientId,
                client_secret: cdseClientSecret
            }).catch(() => {});
        } catch {}
        setCdseUsername('');
        setCdsePassword('');
        setCdseClientId('');
        setCdseClientSecret('');
        setCdseToken('');
        if (!isInstitutionalServer) {
            setCdseConfigured(false);
        }
        setCdseSuccess('Cuenta desconectada exitosamente. Puedes ingresar cualquier otra cuenta.');
        setCdseError(null);
        setProcessLog(prev => [...prev, 'ℹ  Sesión de credenciales CDSE cerrada']);
        setTimeout(() => setCdseSuccess(null), 3500);
    };

    const handleVerifyAccess = async () => {
        const isPass = cdseAuthMode === 'password';
        if (isPass && (!cdseUsername || !cdsePassword)) {
            setCdseError('Ingresa tu email y contraseña de Copernicus para verificar.');
            setCdseSuccess(null);
            return;
        }
        if (!isPass && (!cdseClientId || !cdseClientSecret)) {
            setCdseError('Ingresa tu Client ID y Client Secret de Copernicus OAuth para verificar.');
            setCdseSuccess(null);
            return;
        }
        setVerifyingCreds(true);
        setCdseError(null);
        setCdseSuccess(null);
        const API = getApiUrl();
        try {
            const payload = isPass
                ? { username: cdseUsername, password: cdsePassword }
                : { client_id: cdseClientId, client_secret: cdseClientSecret };
            const res = await axios.post(`${API}/verify-credentials`, payload);
            setCdseSuccess(res.data?.message || 'Acceso verificado exitosamente con Copernicus Hub.');
            setProcessLog(prev => [...prev, '[OK] Acceso a Copernicus CDSE verificado']);
        } catch (err: any) {
            const msg = err.response?.data?.detail || 'Error verificando acceso en Copernicus Hub';
            setCdseError(msg);
        } finally {
            setVerifyingCreds(false);
        }
    };

    const handleSetCredentials = async (e: React.FormEvent) => {
        e.preventDefault();
        setSavingCreds(true);
        setCdseError(null);
        setCdseSuccess(null);
        const API = getApiUrl();
        const isPass = cdseAuthMode === 'password';
        try {
            const payload = isPass
                ? { username: cdseUsername, password: cdsePassword }
                : { client_id: cdseClientId, client_secret: cdseClientSecret };
            const setRes = await axios.post(`${API}/set-credentials`, payload);
            if (setRes.data?.token) {
                setCdseToken(setRes.data.token);
            }
            try {
                sessionStorage.setItem('cdse_credentials', JSON.stringify({
                    authMode: cdseAuthMode,
                    username: cdseUsername,
                    password: cdsePassword,
                    clientId: cdseClientId,
                    clientSecret: cdseClientSecret
                }));
                localStorage.removeItem('cdse_credentials');
            } catch {}
            setCdseConfigured(true);
            setCdseSuccess('Conectado y listo para procesar imágenes Sentinel-2.');
            setProcessLog(prev => [...prev, '[OK] Credenciales CDSE activadas correctamente']);
            setTimeout(() => {
                setShowCredentials(false);
                setCdseSuccess(null);
            }, 1200);
        } catch (err: any) {
            const msg = err.response?.data?.detail || 'Error configurando credenciales';
            setCdseError(msg);
        } finally {
            setSavingCreds(false);
        }
    };

    const handleFileUpload = async (file: File) => {
        setUploadingFile(true);
        setFileError(null);
        const API = getApiUrl();
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await axios.post(`${API}/parse-geometry`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            const { geometry, centroid, bounds } = res.data;
            setCustomArea(geometry);
            setCustomAreaName(file.name.replace(/\.[^.]+$/, ''));
            setAreaSource('custom');
            setSelectedWetland(null);
            setSearchQuery('');
            const optimalZoom = bounds ? getOptimalZoom(bounds) : 12;
            setViewState({
                longitude: centroid.lon,
                latitude: centroid.lat,
                zoom: optimalZoom
            });
            setProcessLog(prev => [...prev, `✓  Área personalizada cargada: ${file.name} (Zoom óptimo: ${optimalZoom})`]);
        } catch (err: any) {
            const msg = err.response?.data?.detail || 'Error al leer el archivo';
            setFileError(msg);
        } finally {
            setUploadingFile(false);
        }
    };

    const fitToWetland = () => {
        let activeBounds: [number, number, number, number] | null = null;
        if (areaSource === 'wetland' && selectedWetland?.bbox) {
            activeBounds = selectedWetland.bbox;
        } else if (areaSource === 'custom' && customArea) {
            activeBounds = getBoundsFromGeometry(customArea);
        } else if (selectedWetland?.geometry) {
            activeBounds = getBoundsFromGeometry(selectedWetland.geometry);
        }

        if (activeBounds) {
            const centerLon = (activeBounds[0] + activeBounds[2]) / 2;
            const centerLat = (activeBounds[1] + activeBounds[3]) / 2;
            const optimalZoom = getOptimalZoom(activeBounds);
            setViewState({
                longitude: centerLon,
                latitude: centerLat,
                zoom: optimalZoom
            });
            setProcessLog(prev => [...prev, `🔍 Vista reajustada a visión total (Zoom: ${optimalZoom})`]);
        }
    };

    const handleAnalyze = async () => {
        if (!cdseConfigured) {
            // Attempt auto-restoring credentials from sessionStorage before aborting
            let restored = false;
            try {
                const saved = sessionStorage.getItem('cdse_credentials') || localStorage.getItem('cdse_credentials');
                if (saved) {
                    const parsed = JSON.parse(saved);
                    const isPass = parsed.authMode !== 'oauth';
                    const hasData = isPass ? (parsed.username && parsed.password) : (parsed.clientId && parsed.clientSecret);
                    if (hasData) {
                        setProcessLog(prev => [...prev, "🔑 Conectando credenciales CDSE..."]);
                        const payload = isPass
                            ? { username: parsed.username, password: parsed.password }
                            : { client_id: parsed.clientId, client_secret: parsed.clientSecret };
                        const authRes = await axios.post(`${getApiUrl()}/set-credentials`, payload);
                        if (authRes.data?.status === 'ok') {
                            if (authRes.data?.token) setCdseToken(authRes.data.token);
                            setCdseConfigured(true);
                            restored = true;
                            setProcessLog(prev => [...prev, "✓  Credenciales CDSE autenticadas"]);
                        }
                    }
                }
            } catch {}

            if (!restored) {
                setShowCredentials(true);
                setError("Debes ingresar y verificar tus credenciales de Copernicus CDSE antes de iniciar el análisis.");
                return;
            }
        }

        const hasArea = areaSource === 'wetland' ? !!selectedWetland : !!customArea;
        if (!hasArea) {
            setError("Selecciona un humedal o carga un área personalizada.");
            return;
        }

        setLoading(true);
        setError(null);
        setResults(null);
        setProcessLog([
            "⚙️  Iniciando análisis multi-espectral...",
            "☀️  Filtro de calidad Sen2Cor SCL activo (Máx. 30% nubes / leastCC)"
        ]);
        setViewYear('end');

        let aoiGeometry: any;
        let activeBounds: [number, number, number, number] | null = null;
        let centerLon = viewState.longitude;
        let centerLat = viewState.latitude;

        if (areaSource === 'custom' && customArea) {
            aoiGeometry = customArea;
            activeBounds = getBoundsFromGeometry(customArea);
        } else if (selectedWetland) {
            if (selectedWetland.geometry) {
                aoiGeometry = selectedWetland.geometry;
            } else if (selectedWetland.bbox) {
                const [minX, minY, maxX, maxY] = selectedWetland.bbox;
                aoiGeometry = { type: "Polygon", coordinates: [[[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY], [minX, minY]]] };
            }
            if (selectedWetland.bbox) {
                activeBounds = selectedWetland.bbox;
            } else if (aoiGeometry) {
                activeBounds = getBoundsFromGeometry(aoiGeometry);
            }
        } else {
            aoiGeometry = { type: "Polygon", coordinates: [[[-70.7, -33.3], [-70.8, -33.3], [-70.8, -33.4], [-70.7, -33.4], [-70.7, -33.3]]] };
        }

        // AUTO-FIT: Al momento de generar los cálculos, abarcar el acercamiento total que permita la visión total del humedal
        if (activeBounds) {
            centerLon = (activeBounds[0] + activeBounds[2]) / 2;
            centerLat = (activeBounds[1] + activeBounds[3]) / 2;
            const optimalZoom = getOptimalZoom(activeBounds);
            setViewState({
                longitude: centerLon,
                latitude: centerLat,
                zoom: optimalZoom
            });
            setProcessLog(prev => [...prev, `🔍 Vista encuadrada para visión total (Zoom óptimo: ${optimalZoom})`]);
        }

        const geomBoundsKey = activeBounds ? `_${activeBounds.map(n => n.toFixed(3)).join(',')}` : '';
        const cacheKey = `${selectedWetland?.name || ('custom' + geomBoundsKey)}_${startDate}_${endDate}`;
        if (localAnalysisCache.current[cacheKey]) {
            setProcessLog(prev => [
                ...prev,
                "⚡ Cargando análisis instantáneo desde memoria local (<10ms)...",
                "✓  Datos multiespectrales listos"
            ]);
            const cachedData = localAnalysisCache.current[cacheKey];
            setResults(cachedData);
            setMergedData(resampleToMonthly(mergeTimeSeries(cachedData)));
            setLoading(false);
            if (activeBounds) {
                const optimalZoom = getOptimalZoom(activeBounds);
                setViewState({
                    longitude: centerLon,
                    latitude: centerLat,
                    zoom: optimalZoom
                });
            }
            return;
        }

        // Cancel previous in-flight CDSE analysis request to avoid race condition and wasted PUs
        if (analysisAbortController.current) {
            analysisAbortController.current.abort();
        }
        const controller = new AbortController();
        analysisAbortController.current = controller;

        const payload = {
            geojson: { type: "Feature", geometry: aoiGeometry },
            startDate,
            endDate,
            wetlandName: selectedWetland?.name || null,
            region: selectedWetland?.region || null,
        };
        setSarData(null);
        setSarError(null);

        try {
            setProcessLog(prev => [...prev, "⚙️  Procesando todos los índices con CDSE (V2 unificada)..."]);
            const headers: Record<string, string> = {};
            if (cdseAuthMode === 'password' && cdseUsername && cdsePassword) {
                headers['X-CDSE-Username'] = cdseUsername;
                headers['X-CDSE-Password'] = cdsePassword;
            } else if (cdseAuthMode === 'oauth' && cdseClientId && cdseClientSecret) {
                headers['X-CDSE-Client-Id'] = cdseClientId;
                headers['X-CDSE-Client-Secret'] = cdseClientSecret;
            }
            // Use analyze-all endpoint with cancellation signal
            const res = await axios.post(`${getApiUrl()}/analyze-all`, payload, { 
                headers, 
                signal: controller.signal 
            });

            if (res.data.status === 'success') {
                const data = res.data.data;
                const allErrors = Object.values(data).every((item: any) => item?.status === 'error');
                if (allErrors) {
                    const firstMsg = (Object.values(data)[0] as any)?.error || "Error al procesar índices satelitales";
                    if (firstMsg.toLowerCase().includes("credentials") || firstMsg.toLowerCase().includes("credenciales")) {
                        setCdseConfigured(false);
                        setShowCredentials(true);
                        setError("Las credenciales de Copernicus CDSE no están activas en el servidor. Por favor ingresa tus credenciales en el panel lateral.");
                    } else {
                        setError(`Error en análisis: ${firstMsg}`);
                    }
                    setProcessLog(prev => [...prev, `✗  ERR: ${firstMsg.substring(0, 40)}...`]);
                    return;
                }

                // Cache data in local memory and sessionStorage for instant zero-lag reload (<10ms)
                localAnalysisCache.current[cacheKey] = data;
                if (typeof window !== 'undefined') {
                    try {
                        sessionStorage.setItem('wetland_v2_analysis_cache', JSON.stringify(localAnalysisCache.current));
                    } catch {}
                }

                // PU Quota Optimization: Preload ONLY the active view overlays (prevents burning 7 extra PUs for start_year until requested)
                if (typeof window !== 'undefined') {
                    const activeUrls = new Set<string>();
                    Object.values(data).forEach((modeRes: any) => {
                        if (modeRes?.maps?.overlay?.metric_url) {
                            activeUrls.add(resolveBackendUrl(modeRes.maps.overlay.metric_url));
                        }
                    });
                    activeUrls.forEach(url => {
                        fetch(url, {
                            headers: cdseToken ? { Authorization: `Bearer ${cdseToken}` } : {}
                        }).catch(() => {});
                    });
                }

                setResults(data);
                setMergedData(resampleToMonthly(mergeTimeSeries(data)));
                setProcessLog(prev => [...prev, "✓  Análisis finalizado exitosamente"]);

                // Re-confirmar encuadre total para los mapas satelitales recién generados
                if (activeBounds) {
                    const optimalZoom = getOptimalZoom(activeBounds);
                    setViewState({
                        longitude: centerLon,
                        latitude: centerLat,
                        zoom: optimalZoom
                    });
                }
            }
        } catch (err: any) {
            if (axios.isCancel(err) || err.name === 'CanceledError' || err.code === 'ERR_CANCELED') {
                console.log("Petición de análisis anterior cancelada");
                return;
            }
            console.error("Analysis error:", err);
            const status = err.response?.status;
            const detail = err.response?.data?.detail || err.message || "Error desconocido";
            if (status === 401 || detail.toLowerCase().includes("credentials") || detail.toLowerCase().includes("credenciales")) {
                setCdseConfigured(false);
                setShowCredentials(true);
                setError("Credenciales de Copernicus CDSE no configuradas o expiradas. Por favor ingresa tus credenciales en el panel lateral.");
            } else {
                setError(`Error en análisis: ${detail}`);
            }
            setProcessLog(prev => [...prev, `✗  ERR: ${detail.substring(0, 35)}...`]);
        } finally {
            if (analysisAbortController.current === controller) {
                setLoading(false);
            }
        }
    };


    const handleAnalyzeSar = async () => {
        if (!activeGeometry) return;
        setSarLoading(true);
        setSarError(null);
        try {
            const headers: Record<string, string> = {};
            if (cdseToken) headers['Authorization'] = `Bearer ${cdseToken}`;
            else if (cdseAuthMode === 'password' && cdseUsername && cdsePassword) {
                headers['X-CDSE-Username'] = cdseUsername;
                headers['X-CDSE-Password'] = cdsePassword;
            } else if (cdseAuthMode === 'oauth' && cdseClientId && cdseClientSecret) {
                headers['X-CDSE-Client-Id'] = cdseClientId;
                headers['X-CDSE-Client-Secret'] = cdseClientSecret;
            }
            const res = await axios.post(`${getApiUrl()}/api/analyze-sar`, {
                geojson: { type: "Feature", geometry: activeGeometry },
                startDate,
                endDate,
                wetlandName: selectedWetland?.name || null,
                region: selectedWetland?.region || null,
            }, { headers });
            setSarData(res.data);
        } catch (err: any) {
            setSarError(err?.response?.data?.detail || 'No se pudo completar el análisis radar Sentinel-1');
        } finally {
            setSarLoading(false);
        }
    };

    const handleDownloadReport = async () => {
        const hasArea = areaSource === 'wetland' ? !!selectedWetland : !!customArea;
        if (!results || !hasArea) {
            alert('Debes ejecutar un análisis primero');
            return;
        }

        try {
            setGeneratingReport(true);
            setProcessLog(prev => [...prev, "📄 Generando informe Word (.docx)..."]);

            const areaName = selectedWetland?.name || customAreaName || 'Area_Personalizada';
            const bbox = (areaSource === 'wetland' && selectedWetland?.bbox)
                ? selectedWetland.bbox
                : (customArea ? getBoundsFromGeometry(customArea) : [-70.8, -33.4, -70.7, -33.3]);
            const centerLat = bbox ? ((bbox[1] + bbox[3]) / 2).toFixed(4) : '-33.0000';
            const centerLon = bbox ? ((bbox[0] + bbox[2]) / 2).toFixed(4) : '-70.0000';

            const reportPayload = {
                wetland_name: areaName,
                wetland_metadata: {
                    region: selectedWetland?.region || 'Zona Personalizada',
                    code: selectedWetland?.code || 'CUSTOM',
                    coordinates: `${centerLat}, ${centerLon}`,
                    geometry: selectedWetland?.geometry || customArea || null,
                    bbox: bbox || null
                },
                analysis_results: results,
                start_date: startDate,
                end_date: endDate
            };

            const headers: Record<string, string> = {};
            if (cdseAuthMode === 'password' && cdseUsername && cdsePassword) {
                headers['X-CDSE-Username'] = cdseUsername;
                headers['X-CDSE-Password'] = cdsePassword;
            } else if (cdseAuthMode === 'oauth' && cdseClientId && cdseClientSecret) {
                headers['X-CDSE-Client-Id'] = cdseClientId;
                headers['X-CDSE-Client-Secret'] = cdseClientSecret;
            }

            const res = await axios.post(`${getApiUrl()}/generate-report`, reportPayload, {
                headers,
                responseType: 'blob'
            });

            // If the server returned an error wrapped in a blob, detect and parse it
            if (res.data?.type === 'application/json' || (res.data instanceof Blob && res.data.type.includes('json'))) {
                const text = await res.data.text();
                let errMsg = 'Error al generar el informe';
                try {
                    const parsed = JSON.parse(text);
                    if (parsed.detail) errMsg = parsed.detail;
                } catch { }
                throw new Error(errMsg);
            }

            // Create explicit DOCX typed blob so Windows and Word recognize the file immediately
            const docxMime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
            const blob = new Blob([res.data], { type: docxMime });

            // Generate clean, safe filename free of path separators, accents, and illegal characters
            const cleanName = areaName
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^a-zA-Z0-9_\-\.]/g, '_')
                .replace(/_+/g, '_')
                .replace(/^_+|_+$/g, '');

            const safeFilename = `Reporte_Comparativo_${cleanName || 'Humedal'}_${startDate}_a_${endDate}.docx`;

            // Download file via invisible link
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = safeFilename;
            document.body.appendChild(link);
            link.click();
            
            setTimeout(() => {
                link.remove();
                window.URL.revokeObjectURL(url);
            }, 1000);

            setProcessLog(prev => [...prev, `✓  Informe descargado: ${safeFilename}`]);
        } catch (err: any) {
            console.error(err);
            const msg = err?.message || 'Error al generar el informe. Revisa la consola para más detalles.';
            alert(msg);
            setProcessLog(prev => [...prev, `✗  Error: ${msg.substring(0, 40)}`]);
        } finally {
            setGeneratingReport(false);
        }
    };

    // Computed active geometry (exact GeoJSON polygon or bbox of wetland/custom area)
    const activeGeometry = React.useMemo(() => {
        if (areaSource === 'custom' && customArea) {
            return customArea;
        }
        if (selectedWetland) {
            if (selectedWetland.geometry) {
                return selectedWetland.geometry;
            }
            if (selectedWetland.bbox) {
                const [minX, minY, maxX, maxY] = selectedWetland.bbox;
                return {
                    type: "Polygon",
                    coordinates: [[[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY], [minX, minY]]]
                };
            }
        }
        return null;
    }, [areaSource, customArea, selectedWetland]);

    // --- RENDER ---
    return (
        <div className="min-h-screen bg-[#050505] text-white flex overflow-hidden font-sans">

            {/* SIDEBAR */}
            <aside className="w-80 h-screen p-6 flex flex-col gap-6 z-20 pointer-events-auto bg-black border-r border-white/5 overflow-y-auto custom-scrollbar">
                <div className="flex items-center gap-3 mb-2">
                    <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center shadow-[0_0_20px_rgba(37,99,235,0.5)]">
                        <Activity className="text-white w-6 h-6" />
                    </div>
                    <h1 className="text-xl font-bold tracking-tight">WETLAND<span className="text-blue-500">MONITOR</span></h1>
                </div>

                {/* 1. CDSE STATUS + CREDENTIALS */}
                <GlassPanel className="p-4 flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                        <div className={`flex items-center gap-2 text-sm font-bold ${cdseConfigured ? 'text-green-400' : 'text-amber-400'}`}>
                            <span className={`w-2 h-2 rounded-full animate-pulse ${cdseConfigured ? 'bg-green-500' : 'bg-amber-500'}`} />
                            {isInstitutionalServer 
                                ? ((cdseUsername || cdseClientId) ? 'CDSE PERSONAL (Activo)' : 'CDSE INSTITUCIONAL')
                                : (cdseConfigured ? 'CDSE ACTIVO' : 'CDSE NO CONFIGURADO')}
                        </div>
                        <button
                            onClick={() => setShowCredentials(v => !v)}
                            className="text-[10px] text-gray-500 hover:text-white flex items-center gap-1 transition-colors"
                        >
                            <Settings className="w-3 h-3" />
                            {showCredentials ? 'Ocultar' : 'Credenciales'}
                        </button>
                    </div>

                    {showCredentials && (
                        <form onSubmit={handleSetCredentials} className="flex flex-col gap-2.5 animate-in fade-in slide-in-from-top-2">
                            {/* Selector de tipo de credencial: Email/Clave vs OAuth Client */}
                            <div className="flex gap-1 bg-black/60 rounded-lg p-1 border border-white/10">
                                <button
                                    type="button"
                                    onClick={() => { setCdseAuthMode('password'); setCdseError(null); }}
                                    className={`flex-1 text-[9px] font-bold py-1.5 rounded transition-all flex items-center justify-center gap-1 ${cdseAuthMode === 'password' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-400 hover:text-white'}`}
                                >
                                    <User className="w-3 h-3" /> Email y Clave
                                </button>
                                <button
                                    type="button"
                                    onClick={() => { setCdseAuthMode('oauth'); setCdseError(null); }}
                                    className={`flex-1 text-[9px] font-bold py-1.5 rounded transition-all flex items-center justify-center gap-1 ${cdseAuthMode === 'oauth' ? 'bg-blue-600 text-white shadow-sm' : 'text-gray-400 hover:text-white'}`}
                                >
                                    <Key className="w-3 h-3" /> OAuth Client
                                </button>
                            </div>

                            {cdseAuthMode === 'password' ? (
                                <>
                                    <div>
                                        <label className="text-[9px] text-gray-400 uppercase font-semibold mb-1 block">Email Copernicus</label>
                                        <input
                                            value={cdseUsername}
                                            onChange={e => { setCdseUsername(e.target.value); setCdseError(null); setCdseSuccess(null); }}
                                            type="email"
                                            placeholder="usuario@ejemplo.com"
                                            autoComplete="username"
                                            className="w-full bg-black border border-white/10 rounded-lg px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-blue-500/60 transition-colors"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-[9px] text-gray-400 uppercase font-semibold mb-1 block">Contraseña</label>
                                        <input
                                            value={cdsePassword}
                                            onChange={e => { setCdsePassword(e.target.value); setCdseError(null); setCdseSuccess(null); }}
                                            type="password"
                                            placeholder="••••••••••••••••"
                                            autoComplete="current-password"
                                            className="w-full bg-black border border-white/10 rounded-lg px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-blue-500/60 transition-colors"
                                        />
                                    </div>
                                </>
                            ) : (
                                <>
                                    <div>
                                        <label className="text-[9px] text-gray-400 uppercase font-semibold mb-1 block">OAuth Client ID</label>
                                        <input
                                            value={cdseClientId}
                                            onChange={e => { setCdseClientId(e.target.value); setCdseError(null); setCdseSuccess(null); }}
                                            type="text"
                                            placeholder="cdse-client-xxxx-xxxx"
                                            className="w-full bg-black border border-white/10 rounded-lg px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-blue-500/60 transition-colors font-mono"
                                        />
                                    </div>
                                    <div>
                                        <label className="text-[9px] text-gray-400 uppercase font-semibold mb-1 block">OAuth Client Secret</label>
                                        <input
                                            value={cdseClientSecret}
                                            onChange={e => { setCdseClientSecret(e.target.value); setCdseError(null); setCdseSuccess(null); }}
                                            type="password"
                                            placeholder="••••••••••••••••"
                                            className="w-full bg-black border border-white/10 rounded-lg px-2.5 py-1.5 text-[11px] text-white outline-none focus:border-blue-500/60 transition-colors font-mono"
                                        />
                                    </div>
                                </>
                            )}

                            {cdseError && (
                                <div className="text-[9px] text-red-400 bg-red-500/10 border border-red-500/20 px-2.5 py-1.5 rounded flex items-start gap-1.5 leading-tight animate-in fade-in">
                                    <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-red-400" />
                                    <span>{cdseError}</span>
                                </div>
                            )}

                            {cdseSuccess && (
                                <div className="text-[9px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1.5 rounded flex items-start gap-1.5 leading-tight animate-in fade-in">
                                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-emerald-400" />
                                    <span>{cdseSuccess}</span>
                                </div>
                            )}

                            <div className="flex gap-2 pt-1">
                                <button
                                    type="button"
                                    onClick={handleVerifyAccess}
                                    disabled={verifyingCreds || savingCreds || (cdseAuthMode === 'password' ? (!cdseUsername || !cdsePassword) : (!cdseClientId || !cdseClientSecret))}
                                    className="flex-1 bg-gray-800 hover:bg-gray-700 border border-white/10 disabled:bg-gray-900/60 disabled:text-gray-600 text-[10px] py-2 rounded-lg font-bold transition-all text-gray-200 flex items-center justify-center gap-1.5"
                                >
                                    {verifyingCreds ? (
                                        <><div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Verificando...</>
                                    ) : (
                                        <><ShieldCheck className="w-3.5 h-3.5 text-blue-400" /> Verificar Acceso</>
                                    )}
                                </button>
                                <button
                                    type="submit"
                                    disabled={savingCreds || verifyingCreds || (cdseAuthMode === 'password' ? (!cdseUsername || !cdsePassword) : (!cdseClientId || !cdseClientSecret))}
                                    className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-800 disabled:text-gray-500 text-[10px] py-2 rounded-lg font-bold transition-all text-white flex items-center justify-center gap-1.5 shadow-[0_0_15px_rgba(37,99,235,0.25)]"
                                >
                                    {savingCreds ? (
                                        <><div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Conectando...</>
                                    ) : (
                                        'Conectar CDSE'
                                    )}
                                </button>
                            </div>

                            {(cdseUsername || cdseClientId || cdseConfigured) && (
                                <button
                                    type="button"
                                    onClick={handleDisconnect}
                                    className="w-full mt-0.5 bg-red-950/40 hover:bg-red-900/50 border border-red-500/20 text-red-300 text-[9px] py-1.5 rounded-lg font-semibold transition-all flex items-center justify-center gap-1"
                                >
                                    <LogOut className="w-3 h-3" /> Desconectar / Cambiar de cuenta
                                </button>
                            )}

                            <p className="text-[8px] text-gray-500 leading-relaxed">
                                Compatible con cualquier cuenta de <span className="text-blue-400 underline">dataspace.copernicus.eu</span> (personal o institucional). Si agotas tu cuota de unidades de procesamiento (PUs), puedes cambiar de cuenta aquí.
                            </p>
                        </form>
                    )}
                </GlassPanel>

                {/* 2. AREA SOURCE TABS + SEARCH / FILE UPLOAD */}
                <GlassPanel className="p-4 flex flex-col gap-3 overflow-visible">
                    <div className="flex gap-1 bg-black/50 rounded-lg p-1">
                        <button
                            onClick={() => setAreaSource('wetland')}
                            className={`flex-1 text-[9px] font-bold uppercase py-1.5 rounded-md transition-all ${areaSource === 'wetland' ? 'bg-blue-600 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                        >Catálogo</button>
                        <button
                            onClick={() => setAreaSource('custom')}
                            className={`flex-1 text-[9px] font-bold uppercase py-1.5 rounded-md transition-all ${areaSource === 'custom' ? 'bg-purple-600 text-white' : 'text-gray-500 hover:text-gray-300'}`}
                        >Área Personalizada</button>
                    </div>

                    {areaSource === 'wetland' ? (
                        <div className="flex flex-col gap-2">
                            {/* Regional Filter Dropdown */}
                            <div className="relative">
                                <select
                                    value={selectedRegion}
                                    onChange={(e) => {
                                        setSelectedRegion(e.target.value);
                                        setShowSuggestions(true);
                                        updateDropdownRect();
                                    }}
                                    className="w-full bg-black/60 border border-white/10 hover:border-white/20 rounded-xl px-3 py-2 text-[11px] text-gray-200 outline-none focus:border-blue-500/50 cursor-pointer font-medium transition-colors"
                                >
                                    <option value="">🗺️ Todas las Regiones ({wetlands.length})</option>
                                    {CHILEAN_REGIONS.map(reg => (
                                        <option key={reg} value={reg} className="bg-gray-950 text-gray-200">
                                            {reg} ({regionCounts[reg] || 0})
                                        </option>
                                    ))}
                                </select>
                            </div>

                            {/* Search Input */}
                            <div className="relative">
                                <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500 pointer-events-none" />
                                <input
                                    ref={searchInputRef}
                                    type="text"
                                    value={searchQuery}
                                    onChange={(e) => {
                                        setSearchQuery(e.target.value);
                                        setShowSuggestions(true);
                                        updateDropdownRect();
                                    }}
                                    onFocus={() => {
                                        updateDropdownRect();
                                        setShowSuggestions(true);
                                    }}
                                    onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
                                    placeholder={selectedRegion ? `Buscar en ${selectedRegion}...` : "Buscar humedal por nombre o código..."}
                                    className="w-full bg-gray-900 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-xs focus:ring-1 focus:ring-blue-500 outline-none placeholder:text-gray-500"
                                />

                                {showSuggestions && filteredWetlands.length > 0 && dropdownRect && createPortal(
                                    <div
                                        style={{
                                            position: 'fixed',
                                            top: dropdownRect.bottom + 4,
                                            left: dropdownRect.left,
                                            width: dropdownRect.width,
                                            zIndex: 99999,
                                        }}
                                        className="bg-gray-950/98 backdrop-blur-2xl border border-white/15 rounded-xl max-h-64 overflow-y-auto shadow-2xl custom-scrollbar"
                                    >
                                        {Object.entries(groupedWetlands).map(([regionName, items]) => (
                                            <div key={regionName} className="border-b border-white/10 last:border-0">
                                                <div className="sticky top-0 z-10 bg-gray-900 px-3 py-1.5 text-[9px] font-bold uppercase tracking-wider text-blue-400 border-b border-white/10 flex justify-between items-center shadow-sm">
                                                    <span>📍 {regionName}</span>
                                                    <span className="text-[8px] text-gray-400 bg-white/5 px-1.5 py-0.5 rounded font-mono">{items.length}</span>
                                                </div>
                                                {items.map(w => (
                                                    <button
                                                        key={w.id || w.name}
                                                        onMouseDown={() => selectWetland(w)}
                                                        className="w-full text-left px-4 py-2 text-[10px] hover:bg-blue-600/20 text-gray-300 hover:text-white transition-colors flex justify-between items-center border-b border-white/5 last:border-0"
                                                    >
                                                        <span className="truncate font-medium">{w.name}</span>
                                                        {w.code && <span className="text-[8px] text-gray-500 font-mono ml-2">#{w.code}</span>}
                                                    </button>
                                                ))}
                                            </div>
                                        ))}
                                    </div>,
                                    document.body
                                )}
                            </div>
                        </div>
                    ) : (
                        <div>
                            <label
                                htmlFor="area-file-input"
                                className={`flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-xl p-4 cursor-pointer transition-all ${uploadingFile ? 'border-purple-500/50 bg-purple-500/5' :
                                    customArea ? 'border-green-500/50 bg-green-500/5' :
                                        'border-white/10 hover:border-purple-500/50 hover:bg-purple-500/5'
                                    }`}
                            >
                                {uploadingFile ? (
                                    <><div className="w-5 h-5 border-2 border-purple-500/30 border-t-purple-400 rounded-full animate-spin" />
                                        <span className="text-[9px] text-purple-300">Procesando...</span></>
                                ) : customArea ? (
                                    <><svg className="w-5 h-5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
                                        <span className="text-[9px] text-green-400 font-bold">{customAreaName || 'Área cargada'}</span>
                                        <span className="text-[8px] text-gray-600">Clic para cambiar archivo</span></>
                                ) : (
                                    <><svg className="w-5 h-5 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" /></svg>
                                        <span className="text-[9px] text-gray-400">Arrastra o selecciona tu área</span>
                                        <span className="text-[8px] text-gray-600">SHP (.zip), GeoJSON, KML, KMZ</span></>
                                )}
                            </label>
                            <input
                                id="area-file-input"
                                type="file"
                                accept=".geojson,.json,.kml,.kmz,.zip"
                                className="hidden"
                                onChange={e => { if (e.target.files?.[0]) handleFileUpload(e.target.files[0]); }}
                            />
                            {fileError && <div className="mt-2 text-[9px] text-red-400 bg-red-400/10 border border-red-400/20 px-2 py-1 rounded">⚠️ {fileError}</div>}
                        </div>
                    )}
                </GlassPanel>


                {/* 2.5 WETLAND PROFILE */}
                {selectedWetland && (
                    <GlassPanel className="p-4 flex flex-col gap-2 animate-in fade-in zoom-in duration-300">
                        <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider flex items-center gap-2">
                            <Maximize2 className="w-3 h-3" /> Perfil del Humedal
                        </label>
                        <div className="grid grid-cols-2 gap-2 mt-1">
                            <div className="bg-white/5 p-2 rounded-lg">
                                <div className="text-[9px] text-gray-500 uppercase">Región</div>
                                <div className="text-[10px] font-medium text-white truncate" title={selectedWetland.region}>
                                    {selectedWetland.region.replace('Región del ', '').replace('Región de ', '')}
                                </div>
                            </div>
                            <div className="bg-white/5 p-2 rounded-lg">
                                <div className="text-[9px] text-gray-500 uppercase">Código</div>
                                <div className="text-[10px] font-medium text-white">{selectedWetland.code || 'N/A'}</div>
                            </div>
                        </div>
                        <div className="bg-white/5 p-2 rounded-lg mt-1">
                            <div className="text-[9px] text-gray-500 uppercase">Coordenadas (Centro)</div>
                            <div className="text-[10px] font-mono text-blue-300">
                                {((selectedWetland.bbox[1] + selectedWetland.bbox[3]) / 2).toFixed(4)},
                                {((selectedWetland.bbox[0] + selectedWetland.bbox[2]) / 2).toFixed(4)}
                            </div>
                        </div>
                    </GlassPanel>
                )}

                {/* 2.6 NETWORK STATUS (Show when no wetland selected) */}
                {!selectedWetland && (
                    <GlassPanel className="p-4 flex flex-col gap-2 animate-in fade-in duration-500 delay-150 relative overflow-hidden group">
                        <div className="absolute top-0 right-0 p-2 opacity-50">
                            <Activity className="w-12 h-12 text-blue-500/10" />
                        </div>
                        <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider flex items-center gap-2">
                            <Wind className="w-3 h-3 text-blue-400" /> Estado de la Red
                        </label>

                        <div className="flex items-end gap-2 mt-2">
                            <div className="text-3xl font-mono font-bold text-white leading-none">
                                {wetlands.length > 0 ? wetlands.length : '---'}
                            </div>
                            <div className="text-[10px] text-gray-500 mb-1 font-medium">Humedales Monitoreados</div>
                        </div>

                        <div className="h-px bg-white/10 my-1" />

                        <div className="space-y-2">
                            <div className="flex items-center justify-between text-[10px]">
                                <span className="text-gray-400">Sentinel-2 L2A</span>
                                <span className="text-green-400 font-mono bg-green-900/30 px-1.5 py-0.5 rounded flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" /> ONLINE
                                </span>
                            </div>
                            <div className="flex items-center justify-between text-[10px]">
                                <span className="text-gray-400">Copernicus CDSE API</span>
                                <span className="text-green-400 font-mono bg-green-900/30 px-1.5 py-0.5 rounded flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" /> ONLINE
                                </span>
                            </div>
                            <div className="flex items-center justify-between text-[10px]">
                                <span className="text-gray-400">Resolución Espacial</span>
                                <span className="text-blue-300 font-mono">10-20m</span>
                            </div>
                            <div className="flex items-center justify-between text-[10px]">
                                <span className="text-gray-400">Actualización</span>
                                <span className="text-blue-300 font-mono">5 días</span>
                            </div>
                        </div>
                    </GlassPanel>
                )}

                {/* 2.7 TEMPORAL COVERAGE (Show when results available) */}
                {results && results['Hydrology']?.coverage && (
                    <GlassPanel className="p-4 flex flex-col gap-2">
                        <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider flex items-center gap-2">
                            <Calendar className="w-3 h-3 text-green-400" /> Cobertura Temporal
                        </label>

                        <div className="grid grid-cols-2 gap-2">
                            <div className="bg-white/5 p-2 rounded-lg">
                                <div className="text-[9px] text-gray-500 uppercase">Días de Datos</div>
                                <div className="text-sm font-mono font-bold text-green-400">
                                    {results['Hydrology'].coverage.coverage_days || 'N/A'}
                                </div>
                            </div>
                            <div className="bg-white/5 p-2 rounded-lg">
                                <div className="text-[9px] text-gray-500 uppercase">Puntos Válidos</div>
                                <div className="text-sm font-mono font-bold text-blue-400">
                                    {results['Hydrology'].stats.data_count || 'N/A'}
                                </div>
                            </div>
                        </div>

                        <div className="bg-white/5 p-2 rounded-lg mt-1">
                            <div className="text-[9px] text-gray-500 uppercase">Período</div>
                            <div className="text-[9px] font-mono text-gray-300">
                                {results['Hydrology'].coverage.start_date} → {results['Hydrology'].coverage.end_date}
                            </div>
                        </div>

                        {/* Quality indicator */}
                        {(() => {
                            const cv = results['Hydrology'].stats.cv || 0;
                            const outliers = results['Hydrology'].stats.outlier_count || 0;
                            let quality = '';
                            let color = '';

                            if (cv < 20 && outliers === 0) {
                                quality = 'Excelente';
                                color = 'text-green-400';
                            } else if (cv < 40 && outliers < 5) {
                                quality = 'Buena';
                                color = 'text-yellow-400';
                            } else {
                                quality = 'Regular';
                                color = 'text-red-400';
                            }

                            return (
                                <div className="flex items-center justify-between mt-1 p-2 bg-white/5 rounded-lg">
                                    <span className="text-[9px] text-gray-500 uppercase">Calidad</span>
                                    <span className={`text-[10px] font-mono font-bold ${color}`}>● {quality}</span>
                                </div>
                            );
                        })()}
                    </GlassPanel>
                )}

                {/* 2.8 ESTADO SITUACIONAL Y SALUD ECOSISTÉMICA CONSOLIDADA (IMCE) */}
                {consolidatedStatus && (
                    <GlassPanel className="p-3.5 flex flex-col gap-2.5 border-emerald-500/20 bg-gradient-to-b from-emerald-950/20 via-black/40 to-black/60 shadow-[0_0_20px_rgba(16,185,129,0.1)]">
                        <div className="flex items-center justify-between">
                            <label className="text-[10px] text-gray-300 font-bold uppercase tracking-wider flex items-center gap-1.5">
                                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                                <span>Salud Ecosistémica (IMCE)</span>
                            </label>
                            <span 
                                className="text-[9px] font-mono font-bold px-1.5 py-0.5 rounded border"
                                style={{
                                    backgroundColor: `${consolidatedStatus.health_color}20`,
                                    borderColor: `${consolidatedStatus.health_color}50`,
                                    color: consolidatedStatus.health_color
                                }}
                            >
                                {consolidatedStatus.health_badge}
                            </span>
                        </div>

                        {/* IMCE Score & Diagnosis Title */}
                        <div className="flex items-baseline justify-between bg-white/5 p-2 rounded-lg">
                            <div>
                                <div className="text-[8.5px] text-gray-400 uppercase font-mono">Índice IMCE (AHP)</div>
                                <div className="text-xl font-mono font-bold text-white flex items-baseline gap-1">
                                    <span>{consolidatedStatus.imce_score ?? consolidatedStatus.weci_score ?? '—'}</span>
                                    {consolidatedStatus.imce_score != null && (
                                        <span className="text-[10px] text-gray-400 font-normal">/ 100</span>
                                    )}
                                </div>
                            </div>
                            <div className="text-right max-w-[130px]">
                                <div className="text-[8.5px] text-gray-400 uppercase font-mono">Diagnóstico</div>
                                <div className="text-[10px] font-semibold text-emerald-300 leading-tight">
                                    {consolidatedStatus.diagnosis.title}
                                </div>
                            </div>
                        </div>

                        {/* Dimensional Progress Bars */}
                        <div className="space-y-1.5 bg-black/40 p-2 rounded-lg border border-white/5 text-[9px] font-mono">
                            <div className="flex items-center justify-between text-gray-300">
                                <span className="flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400" />
                                    <span>Hidrología ({consolidatedStatus.dimensions.hydrology.weight}%)</span>
                                </span>
                                <span className="text-blue-400 font-bold">
                                    {consolidatedStatus.dimensions.hydrology.score != null ? `${consolidatedStatus.dimensions.hydrology.score.toFixed(0)}%` : 'N/D'}
                                </span>
                            </div>
                            <div className="w-full bg-white/10 h-1.5 rounded-full overflow-hidden">
                                <div className="bg-blue-500 h-full rounded-full transition-all duration-500" style={{ width: `${consolidatedStatus.dimensions.hydrology.score ?? 0}%` }} />
                            </div>

                            <div className="flex items-center justify-between text-gray-300 pt-0.5">
                                <span className="flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-green-400" />
                                    <span>Vegetación ({consolidatedStatus.dimensions.vegetation.weight}%)</span>
                                </span>
                                <span className="text-green-400 font-bold">
                                    {consolidatedStatus.dimensions.vegetation.score != null ? `${consolidatedStatus.dimensions.vegetation.score.toFixed(0)}%` : 'N/D'}
                                </span>
                            </div>
                            <div className="w-full bg-white/10 h-1.5 rounded-full overflow-hidden">
                                <div className="bg-green-500 h-full rounded-full transition-all duration-500" style={{ width: `${consolidatedStatus.dimensions.vegetation.score ?? 0}%` }} />
                            </div>

                            <div className="flex items-center justify-between text-gray-300 pt-0.5">
                                <span className="flex items-center gap-1">
                                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400" />
                                    <span>Calidad Agua ({consolidatedStatus.dimensions.water_quality.weight}%)</span>
                                </span>
                                <span className="text-cyan-400 font-bold">
                                    {consolidatedStatus.dimensions.water_quality.weight === 0 ? 'N/A' : (consolidatedStatus.dimensions.water_quality.score != null ? `${consolidatedStatus.dimensions.water_quality.score.toFixed(0)}%` : 'N/D')}
                                </span>
                            </div>
                            <div className="w-full bg-white/10 h-1.5 rounded-full overflow-hidden">
                                <div className="bg-cyan-500 h-full rounded-full transition-all duration-500" style={{ width: `${consolidatedStatus.dimensions.water_quality.score ?? 0}%` }} />
                            </div>
                        </div>


                        {/* Action to open full audit modal */}
                        <button
                            type="button"
                            onClick={() => setShowDiagnosisModal(true)}
                            className="w-full py-1.5 rounded-lg bg-emerald-600/20 hover:bg-emerald-600/35 border border-emerald-500/30 text-[9.5px] font-semibold text-emerald-300 hover:text-white transition-all text-center flex items-center justify-center gap-1"
                        >
                            <span>Dictamen Completo Ley 21.202</span>
                            <span>→</span>
                        </button>
                    </GlassPanel>
                )}

                {/* 3. CONTROL PANEL */}
                <GlassPanel id="control-panel" className="p-4 flex flex-col gap-3 mt-auto">
                    <label className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Control de Misión</label>
                    {/* QUICK HORIZON PRESETS (<1s) */}
                    <div className="flex items-center justify-between gap-1">
                        {[
                            { label: '1A', yrs: 1 },
                            { label: '3A', yrs: 3 },
                            { label: '5A', yrs: 5 },
                            { label: '10A', yrs: 10 }
                        ].map(preset => (
                            <button
                                key={preset.label}
                                type="button"
                                onClick={() => {
                                    const endDt = new Date(endDate || '2026-01-22');
                                    const startDt = new Date(endDt);
                                    startDt.setFullYear(startDt.getFullYear() - preset.yrs);
                                    const newStart = startDt.toISOString().split('T')[0];
                                    setStartDate(newStart);

                                    const bboxKey = (areaSource === 'wetland' && selectedWetland?.bbox)
                                        ? `_${selectedWetland.bbox.map(n => n.toFixed(3)).join(',')}`
                                        : (customArea ? `_${getBoundsFromGeometry(customArea)?.map(n => n.toFixed(3)).join(',') || ''}` : '');
                                    const key = `${selectedWetland?.name || ('custom' + bboxKey)}_${newStart}_${endDate}`;
                                    if (localAnalysisCache.current[key]) {
                                        const cached = localAnalysisCache.current[key];
                                        setResults(cached);
                                        setMergedData(resampleToMonthly(mergeTimeSeries(cached)));
                                        setProcessLog(prev => [
                                            ...prev,
                                            `⚡ Horizonte ${preset.label} cargado al instante (<10ms)`
                                        ]);
                                    }
                                }}
                                className="flex-1 py-1 rounded bg-white/5 hover:bg-blue-600/30 border border-white/10 hover:border-blue-500/40 text-[9px] font-mono text-gray-300 hover:text-white transition-all text-center"
                                title={`Rango de ${preset.yrs} ${preset.yrs === 1 ? 'año' : 'años'}`}
                            >
                                {preset.label}
                            </button>
                        ))}
                    </div>

                    <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                            <label className="text-[10px] text-gray-400 font-medium">Inicio</label>
                            <input
                                type="date"
                                min="2015-07-01"
                                value={startDate}
                                onChange={e => setStartDate(e.target.value)}
                                className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px] text-gray-200 focus:border-blue-500/50 outline-none"
                            />
                        </div>
                        <div className="space-y-1">
                            <label className="text-[10px] text-gray-400 font-medium">Fin</label>
                            <input
                                type="date"
                                min="2015-07-01"
                                value={endDate}
                                onChange={e => setEndDate(e.target.value)}
                                className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px] text-gray-200 focus:border-blue-500/50 outline-none"
                            />
                        </div>
                    </div>
                    <div className="text-[8px] text-gray-500 -mt-1.5 text-center font-mono">
                        Sentinel-2 MSI disponible desde julio 2015
                    </div>

                    {/* FILTRO NUBOSIDAD INDICATOR (V2 CALIBRATED) */}
                    <div className="flex items-center justify-between px-2.5 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-[9px] font-mono text-emerald-300">
                        <span className="flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                            Filtro Nubes (SCL):
                        </span>
                        <span className="font-bold text-white bg-emerald-500/25 px-1.5 py-0.5 rounded border border-emerald-400/30 text-[8px]">
                            CALIBRADO (&le;30% + SCL)
                        </span>
                    </div>

                    <button
                        type="button"
                        onClick={handleAnalyze}
                        disabled={loading || generatingReport || !(areaSource === 'wetland' ? selectedWetland : customArea)}
                        className={`w-full py-3 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${loading || !(areaSource === 'wetland' ? selectedWetland : customArea) ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_15px_rgba(37,99,235,0.4)]'}`}
                        title={!(areaSource === 'wetland' ? selectedWetland : customArea) ? 'Selecciona un humedal o sube un área personalizada primero' : ''}
                    >
                        {loading ? (
                            <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> PROCESANDO ANÁLISIS...</>
                        ) : (
                            <><Layers className="w-4 h-4" /> EJECUTAR TODO</>
                        )}
                    </button>

                    {/* DOWNLOAD REPORT BUTTON */}
                    <button
                        type="button"
                        onClick={handleDownloadReport}
                        disabled={generatingReport || loading || !results || !(areaSource === 'wetland' ? selectedWetland : customArea)}
                        className={`w-full py-2.5 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${generatingReport || !results || !(areaSource === 'wetland' ? selectedWetland : customArea) ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)]'}`}
                        title={!results ? 'Ejecuta un análisis primero' : 'Descargar informe Word (.docx)'}
                    >
                        {generatingReport ? (
                            <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> GENERANDO INFORME WORD...</>
                        ) : (
                            <>
                                <FileText className="w-4 h-4 text-blue-300" />
                                DESCARGAR INFORME (.DOCX)
                            </>
                        )}
                    </button>

                    <div className="bg-black/50 p-3 rounded border border-white/5 font-mono text-[9px] h-28 overflow-y-auto flex flex-col-reverse custom-scrollbar">
                        {processLog.map((l, i) => (
                            <div
                                key={i}
                                className={`py-0.5 ${l.includes('✓') ? 'text-green-400' :
                                    l.includes('✗') ? 'text-red-400' :
                                        l.includes('⚙️') ? 'text-blue-400' :
                                            i === processLog.length - 1 ? 'text-white' : 'text-gray-600'
                                    }`}
                            >
                                {l}
                            </div>
                        ))}
                    </div>


                </GlassPanel>
            </aside>

            {/* MAIN DASHBOARD */}
            <main className="flex-1 h-screen overflow-hidden flex flex-col relative bg-gradient-to-br from-gray-900 to-black p-3 gap-3">

                {/* TOP CONTROL BAR: Wetland info, Full Extent button, Period Switcher */}
                <div className="flex items-center justify-between bg-black/50 backdrop-blur-xl border border-white/10 px-3.5 py-1.5 rounded-xl shrink-0 shadow-lg">
                    <div className="flex items-center gap-3">
                        <span className="text-[10px] font-bold tracking-wider text-gray-400 uppercase">Humedal Activo:</span>
                        <span className="text-xs font-semibold text-white bg-blue-500/15 border border-blue-500/30 px-2.5 py-0.5 rounded-lg flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse" />
                            {selectedWetland ? selectedWetland.name : customArea ? (customAreaName || 'Área Personalizada') : 'Ninguno seleccionado'}
                        </span>
                        {/* IMCE STATUS PILL */}
                        {consolidatedStatus && (
                            <button
                                type="button"
                                onClick={() => setShowDiagnosisModal(true)}
                                className="text-xs font-semibold px-2.5 py-0.5 rounded-lg flex items-center gap-1.5 border backdrop-blur-md cursor-pointer hover:scale-[1.02] transition-all shadow-sm"
                                style={{
                                    backgroundColor: `${consolidatedStatus.health_color}18`,
                                    borderColor: `${consolidatedStatus.health_color}50`,
                                    color: consolidatedStatus.health_color
                                }}
                                title="Ver Dictamen y Diagnóstico Ecosistémico Consolidado IMCE (Ley 21.202)"
                            >
                                <ShieldCheck className="w-3.5 h-3.5" />
                                <span>IMCE: {consolidatedStatus.imce_score ?? consolidatedStatus.weci_score ?? 'N/D'} · {consolidatedStatus.health_badge}</span>
                            </button>
                        )}
                        {/* CONTROLES ZOOM MANUAL */}
                        <div className="flex items-center bg-black/60 border border-white/10 rounded-lg p-0.5" title="Ajuste fino de acercamiento">
                            <button
                                onClick={() => setViewState(prev => ({ ...prev, zoom: Math.max(3, Math.min(18, Number((prev.zoom - 0.5).toFixed(1)))) }))}
                                className="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-white hover:bg-white/10 rounded font-bold text-xs transition-all"
                                title="Alejar (-0.5)"
                            >
                                −
                            </button>
                            <span className="px-1.5 text-[10px] font-mono text-gray-300 min-w-[2.6rem] text-center">
                                {viewState.zoom.toFixed(1)}z
                            </span>
                            <button
                                onClick={() => setViewState(prev => ({ ...prev, zoom: Math.max(3, Math.min(18, Number((prev.zoom + 0.5).toFixed(1)))) }))}
                                className="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-white hover:bg-white/10 rounded font-bold text-xs transition-all"
                                title="Acercar (+0.5)"
                            >
                                +
                            </button>
                        </div>
                    </div>

                    <div className="flex items-center gap-2.5">
                        {/* BOTÓN VISIÓN TOTAL (RE-CENTRAR Y FIT AL HUMEDAL) */}
                        <button
                            onClick={fitToWetland}
                            disabled={!selectedWetland && !customArea}
                            className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-semibold transition-all shadow-sm ${
                                selectedWetland || customArea
                                    ? 'bg-emerald-600/25 hover:bg-emerald-600/35 text-emerald-300 border border-emerald-500/40 hover:border-emerald-500/60 cursor-pointer shadow-[0_0_12px_rgba(16,185,129,0.2)]'
                                    : 'bg-white/5 text-gray-500 border border-white/5 cursor-not-allowed'
                            }`}
                            title="Ajustar zoom al acercamiento total que permite la visión completa del humedal"
                        >
                            <Maximize2 className="w-3.5 h-3.5" />
                            <span>Visión Total</span>
                        </button>

                        {/* SELECTOR MAPA BASE (SATELITAL / OSCURO) */}
                        <div className="flex items-center bg-black/70 border border-white/10 rounded-lg p-0.5 text-xs">
                            <button
                                onClick={() => setMapMode('satellite')}
                                className={`px-2.5 py-0.5 rounded-md transition-all text-[11px] flex items-center gap-1 ${
                                    mapMode === 'satellite'
                                        ? 'bg-emerald-600 text-white font-semibold shadow-[0_0_10px_rgba(16,185,129,0.4)]'
                                        : 'text-gray-400 hover:text-white'
                                }`}
                                title="Mapa base satelital de alta resolución"
                            >
                                🛰️ Satelital
                            </button>
                            <button
                                onClick={() => setMapMode('dark')}
                                className={`px-2.5 py-0.5 rounded-md transition-all text-[11px] flex items-center gap-1 ${
                                    mapMode === 'dark'
                                        ? 'bg-purple-600 text-white font-semibold shadow-[0_0_10px_rgba(147,51,234,0.4)]'
                                        : 'text-gray-400 hover:text-white'
                                }`}
                                title="Mapa base oscuro de alto contraste"
                            >
                                🌑 Oscuro
                            </button>
                        </div>

                        {/* SELECTOR PERÍODO COMPARATIVO (START / END YEAR) */}
                        <div className="flex items-center bg-black/70 border border-white/10 rounded-lg p-0.5 text-xs">
                            <button
                                onClick={() => setViewYear('start')}
                                className={`px-2.5 py-0.5 rounded-md transition-all text-[11px] ${
                                    viewYear === 'start'
                                        ? 'bg-blue-600 text-white font-semibold shadow-[0_0_10px_rgba(37,99,235,0.4)]'
                                        : 'text-gray-400 hover:text-white'
                                }`}
                            >
                                Inicial ({startDate.substring(0, 4)})
                            </button>
                            <button
                                onClick={() => setViewYear('end')}
                                className={`px-2.5 py-0.5 rounded-md transition-all text-[11px] ${
                                    viewYear === 'end'
                                        ? 'bg-blue-600 text-white font-semibold shadow-[0_0_10px_rgba(37,99,235,0.4)]'
                                        : 'text-gray-400 hover:text-white'
                                }`}
                            >
                                Final ({endDate.substring(0, 4)})
                            </button>
                        </div>
                    </div>
                </div>

                {/* TOP GRID: 6 MAP CARDS (2 rows x 3 cols) */}
                <div className="grid grid-cols-3 gap-3 flex-1 min-h-0">
                    {[
                        { id: 'Hydrology', title: 'HIDROLOGÍA', acronym: 'MNDWI', icon: Droplets, color: 'text-blue-400', border: 'border-blue-500/20' },
                        { id: 'Vegetation', title: 'VEGETACIÓN', acronym: 'NDRE', icon: Activity, color: 'text-green-400', border: 'border-green-500/20' },
                        { id: 'WaterQuality', title: 'CALIDAD AGUA', acronym: 'NDCI', icon: AlertCircle, color: 'text-yellow-400', border: 'border-yellow-500/20' },
                        { id: 'SoilVegetation', title: 'VEG./SUELO', acronym: 'SAVI', icon: Layers, color: 'text-lime-400', border: 'border-lime-500/20' },
                        { id: 'AlgaeBloom', title: 'ALGAS', acronym: 'FAI', icon: Wind, color: 'text-cyan-400', border: 'border-cyan-500/20' },
                        { id: 'WaterRatio', title: 'RATIO AGUA', acronym: 'WRI', icon: Droplets, color: 'text-purple-400', border: 'border-purple-500/20' }
                    ].map(mode => (
                        <IndexCard
                            key={mode.id}
                            mode={mode}
                            res={results?.[mode.id]}
                            legend={LEGENDS[mode.id]}
                            viewState={viewState}
                            onMove={handleMapMove}
                            viewYear={viewYear}
                            boundaryGeometry={activeGeometry}
                            mapMode={mapMode}
                            cdseToken={cdseToken}
                        />
                    ))}
                </div>

                {/* BOTTOM CHART */}
                <div className="h-[25%] shrink-0 bg-black/40 border border-white/10 rounded-2xl p-3 flex flex-col min-h-0">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <Activity className="w-4 h-4 text-purple-400" />
                            <h3 className="text-xs font-bold uppercase tracking-widest text-gray-400">Análisis Temporal</h3>
                        </div>
                        <div className="flex gap-3 flex-wrap">
                            <div className="flex items-center gap-2 text-[10px] text-gray-400"><span className="w-2 h-2 rounded-full bg-blue-500" /> MNDWI</div>
                            <div className="flex items-center gap-2 text-[10px] text-gray-400"><span className="w-2 h-2 rounded-full bg-green-500" /> NDRE</div>
                            <div className="flex items-center gap-2 text-[10px] text-gray-400"><span className="w-2 h-2 rounded-full bg-yellow-500" /> NDCI</div>
                            <div className="flex items-center gap-2 text-[10px] text-lime-400"><span className="w-2 h-2 rounded-full bg-lime-500" /> SAVI</div>
                            <div className="flex items-center gap-2 text-[10px] text-cyan-400"><span className="w-2 h-2 rounded-full bg-cyan-500" /> FAI</div>
                            <div className="flex items-center gap-2 text-[10px] text-purple-400"><span className="w-2 h-2 rounded-full bg-purple-500" /> WRI</div>
                            <div className="flex items-center gap-2 text-[10px] text-red-400"><span className="w-2 h-2 rounded-full bg-red-500" /> Outliers</div>
                        </div>
                    </div>
                    <div className="flex-1 w-full min-h-0">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={mergedData}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" vertical={false} />
                                <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: '#666' }} />
                                <YAxis
                                    axisLine={false}
                                    tickLine={false}
                                    tick={{ fontSize: 10, fill: '#666' }}
                                    domain={['auto', 'auto']}
                                    tickFormatter={(val: any) => typeof val === 'number' ? val.toFixed(3) : val}
                                />
                                <Tooltip
                                    contentStyle={{
                                        backgroundColor: '#0a0f1d',
                                        border: '1px solid rgba(255,255,255,0.15)',
                                        borderRadius: '8px',
                                        fontSize: '11px',
                                        padding: '8px 12px',
                                        boxShadow: '0 8px 24px rgba(0,0,0,0.6)'
                                    }}
                                    itemStyle={{ padding: '2px 0' }}
                                    formatter={(value: any, name: string) => {
                                        if (typeof value === 'number') {
                                            return [value.toFixed(3), METRIC_NAMES[name] || name];
                                        }
                                        return [value, METRIC_NAMES[name] || name];
                                    }}
                                    labelFormatter={(label) => `Período: ${label}`}
                                />

                                {/* DATA LINES */}
                                <Area type="monotone" dataKey="Hydrology" stroke="#3b82f6" fillOpacity={0.1} fill="#3b82f6" strokeWidth={2} connectNulls />
                                <Area type="monotone" dataKey="Vegetation" stroke="#22c55e" fillOpacity={0.1} fill="#22c55e" strokeWidth={2} connectNulls />
                                <Area type="monotone" dataKey="WaterQuality" stroke="#eab308" fillOpacity={0.1} fill="#eab308" strokeWidth={2} connectNulls />
                                <Area type="monotone" dataKey="SoilVegetation" stroke="#84cc16" fillOpacity={0.1} fill="#84cc16" strokeWidth={2} connectNulls />
                                <Area type="monotone" dataKey="AlgaeBloom" stroke="#06b6d4" fillOpacity={0.1} fill="#06b6d4" strokeWidth={2} connectNulls />
                                <Area type="monotone" dataKey="WaterRatio" stroke="#a855f7" fillOpacity={0.1} fill="#a855f7" strokeWidth={2} connectNulls />

                                {/* OUTLIER VISUALIZATION */}
                                {mergedData.map((point, idx) => {
                                    const dots = [];
                                    if (point.Hydrology_outlier && point.Hydrology !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`h-${idx}`}
                                                x={point.date}
                                                y={point.Hydrology}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    if (point.Vegetation_outlier && point.Vegetation !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`v-${idx}`}
                                                x={point.date}
                                                y={point.Vegetation}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    if (point.WaterQuality_outlier && point.WaterQuality !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`w-${idx}`}
                                                x={point.date}
                                                y={point.WaterQuality}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    if (point.SoilVegetation_outlier && point.SoilVegetation !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`s-${idx}`}
                                                x={point.date}
                                                y={point.SoilVegetation}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    if (point.AlgaeBloom_outlier && point.AlgaeBloom !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`a-${idx}`}
                                                x={point.date}
                                                y={point.AlgaeBloom}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    if (point.WaterRatio_outlier && point.WaterRatio !== null) {
                                        dots.push(
                                            <ReferenceDot
                                                key={`wr-${idx}`}
                                                x={point.date}
                                                y={point.WaterRatio}
                                                r={5}
                                                fill="#ef4444"
                                                stroke="white"
                                                strokeWidth={2}
                                            />
                                        );
                                    }
                                    return dots;
                                })}

                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* 5. MODAL: DICTAMEN CIENTÍFICO Y DIAGNÓSTICO ECOSISTÉMICO CONSOLIDADO (LEY 21.202) */}
                {showDiagnosisModal && consolidatedStatus && (
                    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-md animate-in fade-in duration-200">
                        <div className="bg-gray-900/95 border border-white/15 rounded-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto shadow-2xl flex flex-col p-6 gap-5">
                            {/* Modal Header */}
                            <div className="flex items-center justify-between border-b border-white/10 pb-4">
                                <div className="flex items-center gap-2.5">
                                    <div className="p-2 rounded-xl bg-blue-500/20 text-blue-400 border border-blue-500/30">
                                        <ShieldCheck className="w-5 h-5" />
                                    </div>
                                    <div>
                                        <h2 className="text-base font-bold text-white flex items-center gap-2">
                                            <span>Diagnóstico Situacional y Salud Ecosistémica Consolidada</span>
                                            <span 
                                                className="text-[10px] font-mono font-bold px-2 py-0.5 rounded border"
                                                style={{
                                                    backgroundColor: `${consolidatedStatus.health_color}20`,
                                                    borderColor: `${consolidatedStatus.health_color}50`,
                                                    color: consolidatedStatus.health_color
                                                }}
                                            >
                                                {consolidatedStatus.health_badge}
                                            </span>
                                        </h2>
                                        <p className="text-xs text-gray-400">
                                            Humedal: <span className="text-white font-semibold">{selectedWetland?.name || customAreaName || 'Área de Estudio'}</span> · Período: {startDate} → {endDate}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    onClick={() => setShowDiagnosisModal(false)}
                                    className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-white/10 transition-colors"
                                    title="Cerrar modal"
                                >
                                    <X className="w-5 h-5" />
                                </button>
                            </div>

                            {/* IMCE Overview Card */}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                <div className="bg-black/40 border border-white/10 rounded-xl p-4 flex flex-col justify-between">
                                    <span className="text-[10px] text-gray-400 uppercase font-mono font-bold">Índice IMCE Consolidado</span>
                                    <div className="flex items-baseline gap-2 my-2">
                                        <span className="text-4xl font-bold font-mono text-white">{consolidatedStatus.imce_score ?? consolidatedStatus.weci_score ?? '—'}</span>
                                        {consolidatedStatus.imce_score != null && (
                                            <span className="text-xs text-gray-400 font-mono">/ 100</span>
                                        )}
                                    </div>
                                    <div className="text-[11px] font-semibold" style={{ color: consolidatedStatus.health_color }}>
                                        {consolidatedStatus.health_label}
                                    </div>
                                    <div className="text-[9px] text-gray-500 font-mono mt-1">
                                        Modelo AHP · Ramsar Res. IX.1
                                    </div>
                                </div>

                                <div className="md:col-span-2 bg-black/40 border border-white/10 rounded-xl p-4 flex flex-col justify-between">
                                    <div>
                                        <span className="text-[10px] text-gray-400 uppercase font-mono font-bold">Diagnóstico Dinámico (Matriz de Estado-Transición)</span>
                                        <h3 className="text-sm font-bold text-white mt-1">{consolidatedStatus.diagnosis.title}</h3>
                                        <p className="text-xs text-gray-300 mt-1 leading-relaxed">{consolidatedStatus.diagnosis.description}</p>
                                    </div>
                                    <div className="mt-3 pt-2.5 border-t border-white/10 flex items-center justify-between text-[11px]">
                                        <span className="text-gray-400 font-medium">Dictamen Ley 21.202 / SMA:</span>
                                        <span className="text-emerald-400 font-semibold">{consolidatedStatus.diagnosis.regulatory_verdict}</span>
                                    </div>
                                </div>
                            </div>

                            {/* 3 Functional Dimensions */}
                            <div className="space-y-2">
                                <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
                                    <Activity className="w-3.5 h-3.5 text-blue-400" /> Desglose por Dimensiones Ecológicas
                                </h3>
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                                    {/* Hidrología */}
                                    <div className="bg-black/30 border border-blue-500/20 rounded-xl p-3.5 space-y-2">
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs font-bold text-blue-400 flex items-center gap-1.5">
                                                <Droplets className="w-3.5 h-3.5" /> Hidrología y Saturación
                                            </span>
                                            <span className="text-xs font-mono font-bold text-white">
                                                {consolidatedStatus.dimensions.hydrology.score != null ? `${consolidatedStatus.dimensions.hydrology.score.toFixed(0)}%` : 'N/D'}
                                            </span>
                                        </div>
                                        <div className="w-full bg-white/10 h-2 rounded-full overflow-hidden">
                                            <div className="bg-blue-500 h-full rounded-full" style={{ width: `${consolidatedStatus.dimensions.hydrology.score ?? 0}%` }} />
                                        </div>
                                        <div className="text-[10px] text-gray-400">
                                            Indicadores: <span className="text-gray-200 font-mono">MNDWI, WRI</span> · Peso: 35%
                                        </div>
                                    </div>

                                    {/* Vegetación */}
                                    <div className="bg-black/30 border border-green-500/20 rounded-xl p-3.5 space-y-2">
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs font-bold text-green-400 flex items-center gap-1.5">
                                                <Activity className="w-3.5 h-3.5" /> Vigor y Cobertura Vegetal
                                            </span>
                                            <span className="text-xs font-mono font-bold text-white">
                                                {consolidatedStatus.dimensions.vegetation.score != null ? `${consolidatedStatus.dimensions.vegetation.score.toFixed(0)}%` : 'N/D'}
                                            </span>
                                        </div>
                                        <div className="w-full bg-white/10 h-2 rounded-full overflow-hidden">
                                            <div className="bg-green-500 h-full rounded-full" style={{ width: `${consolidatedStatus.dimensions.vegetation.score ?? 0}%` }} />
                                        </div>
                                        <div className="text-[10px] text-gray-400">
                                            Indicadores: <span className="text-gray-200 font-mono">NDRE, SAVI</span> · Peso: 35%
                                        </div>
                                    </div>

                                    {/* Calidad de Agua */}
                                    <div className="bg-black/30 border border-cyan-500/20 rounded-xl p-3.5 space-y-2">
                                        <div className="flex items-center justify-between">
                                            <span className="text-xs font-bold text-cyan-400 flex items-center gap-1.5">
                                                <AlertCircle className="w-3.5 h-3.5" /> Integridad de Calidad
                                            </span>
                                            <span className="text-xs font-mono font-bold text-white">
                                                {consolidatedStatus.dimensions.water_quality.weight === 0 ? 'N/A' : (consolidatedStatus.dimensions.water_quality.score != null ? `${consolidatedStatus.dimensions.water_quality.score.toFixed(0)}%` : 'N/D')}
                                            </span>
                                        </div>
                                        <div className="w-full bg-white/10 h-2 rounded-full overflow-hidden">
                                            <div className="bg-cyan-500 h-full rounded-full" style={{ width: `${consolidatedStatus.dimensions.water_quality.score ?? 0}%` }} />
                                        </div>
                                        <div className="text-[10px] text-gray-400">
                                            Indicadores: <span className="text-gray-200 font-mono">NDCI, FAI</span> · Peso: 30%
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Table of Status by Index */}
                            <div className="space-y-2">
                                <h3 className="text-xs font-bold uppercase tracking-wider text-gray-400 flex items-center gap-1.5">
                                    <Layers className="w-3.5 h-3.5 text-purple-400" /> Matriz Comparativa de Estados por Índice Espectral
                                </h3>
                                <div className="border border-white/10 rounded-xl overflow-hidden bg-black/40">
                                    <table className="w-full text-left border-collapse text-xs">
                                        <thead>
                                            <tr className="bg-white/5 border-b border-white/10 text-gray-400 font-mono text-[10px] uppercase">
                                                <th className="py-2.5 px-3">Índice</th>
                                                <th className="py-2.5 px-3">Inicial ({startDate.substring(0, 4)})</th>
                                                <th className="py-2.5 px-3">Final ({endDate.substring(0, 4)})</th>
                                                <th className="py-2.5 px-3">Variación (Δ)</th>
                                                <th className="py-2.5 px-3">Estado Situacional Declarado</th>
                                                <th className="py-2.5 px-3">Umbral / Referencia</th>
                                            </tr>
                                        </thead>
                                        <tbody className="divide-y divide-white/5 font-mono">
                                            {[
                                                { id: 'Hydrology', name: 'MNDWI', full: 'Hidrología' },
                                                { id: 'Vegetation', name: 'NDRE', full: 'Vegetación RedEdge' },
                                                { id: 'WaterQuality', name: 'NDCI', full: 'Calidad de Agua' },
                                                { id: 'SoilVegetation', name: 'SAVI', full: 'Vegetación Ajustada' },
                                                { id: 'AlgaeBloom', name: 'FAI', full: 'Algas Flotantes' },
                                                { id: 'WaterRatio', name: 'WRI', full: 'Ratio Agua-Tierra' }
                                            ].map(idx => {
                                                const mRes = results?.[idx.id];
                                                const iVal = mRes?.stats?.initial ?? mRes?.stats?.current;
                                                const fVal = mRes?.stats?.current;
                                                const delta = mRes?.stats?.trend_delta ?? mRes?.stats?.trend;
                                                const status = mRes?.situational_status || {
                                                    status_label: 'Sin Datos',
                                                    status_level: 'unknown',
                                                    status_color: '#94a3b8',
                                                    threshold: 'N/A',
                                                    scientific_basis: 'Sin observaciones',
                                                    description: 'Sin datos suficientes en el período',
                                                };
                                                return (
                                                    <tr key={idx.id} className="hover:bg-white/5 transition-colors">
                                                        <td className="py-2.5 px-3 font-bold text-white">
                                                            <span>{idx.name}</span>
                                                            <span className="block text-[9px] font-normal text-gray-400">{idx.full}</span>
                                                        </td>
                                                        <td className="py-2.5 px-3 text-gray-300">
                                                            {iVal != null ? iVal.toFixed(3) : '---'}
                                                        </td>
                                                        <td className="py-2.5 px-3 font-bold text-white">
                                                            {fVal != null ? fVal.toFixed(3) : '---'}
                                                        </td>
                                                        <td className="py-2.5 px-3">
                                                            {delta != null ? (
                                                                <span className={delta >= 0 ? 'text-green-400 font-bold' : 'text-red-400 font-bold'}>
                                                                    {delta >= 0 ? '+' : ''}{delta.toFixed(3)}
                                                                </span>
                                                            ) : '---'}
                                                        </td>
                                                        <td className="py-2.5 px-3">
                                                            <span
                                                                className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-bold border"
                                                                style={{
                                                                    backgroundColor: `${status.status_color}20`,
                                                                    borderColor: `${status.status_color}50`,
                                                                    color: status.status_color
                                                                }}
                                                            >
                                                                <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: status.status_color }} />
                                                                {status.status_label}
                                                            </span>
                                                        </td>
                                                        <td className="py-2.5 px-3 text-[10px] text-gray-400 font-sans">
                                                            <div className="font-mono text-gray-300">{status.threshold}</div>
                                                            <div className="text-[9px] text-gray-500">{status.scientific_basis}</div>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </div>

                            {/* Scientific Protocol and Legal Limitations Box */}
                            <div className="bg-black/50 border border-blue-500/20 rounded-xl p-3 text-[10px] text-gray-400 space-y-1.5 leading-relaxed">
                                <div className="font-bold text-gray-300 font-mono flex items-center gap-1.5">
                                    <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
                                    <span>Protocolo Científico y Alcances Metodológicos (Ley N° 21.202)</span>
                                </div>
                                <p>
                                    El Índice Multicriterio de Condición Ecológica (IMCE) consolida los 6 estimadores biofísicos de Sentinel-2 MSI (10-20m) mediante Análisis Jerárquico (AHP, Saaty 1980) y funciones continuas no lineales.
                                    La dimensión de calidad óptica está físicamente condicionada a la presencia de lámina de agua libre detectable (MNDWI ≥ -0.15) para evitar falsos positivos provocados por vegetación terrestre en periodos de estiaje.
                                    La teledetección acredita de forma continua los criterios de régimen hidrológico y vegetación hidrófita, debiendo complementarse con barrenados de suelo hídrico en terreno para deslindes catastrales definitivos.
                                </p>
                            </div>

                            {/* Trazabilidad Técnica y Auditoría + Radar Sentinel-1 */}
                            <div className="bg-black/40 border border-white/10 rounded-xl p-4 space-y-3">
                                <div className="flex items-center justify-between gap-2 flex-wrap">
                                    <h4 className="text-[11px] font-bold uppercase tracking-wider text-cyan-400">
                                        Trazabilidad Técnica y Evidencia Satelital
                                    </h4>
                                    <button
                                        type="button"
                                        onClick={handleAnalyzeSar}
                                        disabled={sarLoading || !activeGeometry}
                                        className="px-3 py-1 rounded-lg bg-cyan-600/20 hover:bg-cyan-600/35 border border-cyan-500/40 text-cyan-300 text-[10px] font-bold transition-all disabled:opacity-50"
                                    >
                                        {sarLoading ? 'Consultando Radar S1...' : 'Radar Sentinel-1 SAR (Anti-Nubes)'}
                                    </button>
                                </div>
                                {(results as any)?.audit_trail && (() => {
                                    const audit = (results as any).audit_trail;
                                    return (
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px] font-mono">
                                        <div className="bg-white/5 rounded-lg p-2">
                                            <div className="text-gray-500 uppercase">Macrozona</div>
                                            <div className="text-gray-200 truncate" title={audit.macrozone_label || audit.macrozone}>
                                                {audit.macrozone || 'centro_mediterraneo'}
                                            </div>
                                        </div>
                                        <div className="bg-white/5 rounded-lg p-2">
                                            <div className="text-gray-500 uppercase">Superficie</div>
                                            <div className="text-gray-200">
                                                {audit.area_ha != null ? `${audit.area_ha} ha` : 'N/D'}
                                            </div>
                                        </div>
                                        <div className="bg-white/5 rounded-lg p-2">
                                            <div className="text-gray-500 uppercase">Cobertura Sin Nubes</div>
                                            <div className="text-gray-200">
                                                {audit.mean_cloud_free_pct != null ? `${audit.mean_cloud_free_pct}%` : 'N/D'}
                                            </div>
                                        </div>
                                        <div className="bg-white/5 rounded-lg p-2">
                                            <div className="text-gray-500 uppercase">SHA-256 AOI</div>
                                            <div className="text-gray-200 truncate" title={audit.geometry_sha256}>
                                                {audit.geometry_sha256 ? `${audit.geometry_sha256.slice(0, 14)}…` : 'N/D'}
                                            </div>
                                        </div>
                                    </div>
                                    );
                                })()}
                                {sarError && (
                                    <div className="text-[10px] text-red-400 font-mono">{sarError}</div>
                                )}
                                {sarData && (
                                    <div className="grid grid-cols-3 gap-2 text-[10px] font-mono bg-cyan-950/25 border border-cyan-500/20 rounded-lg p-2.5">
                                        <div>
                                            <div className="text-cyan-400/80 uppercase">Observaciones S1</div>
                                            <div className="text-white font-bold">{sarData.observations} pasadas</div>
                                        </div>
                                        <div>
                                            <div className="text-cyan-400/80 uppercase">Lámina Agua Radar (VV &lt; -15dB)</div>
                                            <div className="text-white font-bold">
                                                {sarData.summary?.latest_water_pct != null ? `${sarData.summary.latest_water_pct}%` : 'N/D'}
                                            </div>
                                        </div>
                                        <div>
                                            <div className="text-cyan-400/80 uppercase">Backscatter Medio VV</div>
                                            <div className="text-white font-bold">
                                                {sarData.summary?.mean_vv_db != null ? `${sarData.summary.mean_vv_db} dB` : 'N/D'}
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </div>

                            {/* Footer Actions */}
                            <div className="flex items-center justify-between border-t border-white/10 pt-4">
                                <div className="text-[10px] text-gray-400 font-mono">
                                    Base científica: Sentinel-2 MSI L2A (CDSE) · AHP Multicriterio · Marco Ramsar
                                </div>
                                <div className="flex items-center gap-2">
                                    <button
                                        type="button"
                                        onClick={handleDownloadReport}
                                        disabled={generatingReport}
                                        className="px-4 py-2 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-bold transition-all flex items-center gap-1.5 shadow-[0_0_15px_rgba(37,99,235,0.3)]"
                                    >
                                        <FileText className="w-3.5 h-3.5" />
                                        <span>Descargar Reporte Word (.docx)</span>
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setShowDiagnosisModal(false)}
                                        className="px-4 py-2 rounded-xl bg-white/10 hover:bg-white/15 text-gray-300 text-xs font-semibold transition-all"
                                    >
                                        Cerrar
                                    </button>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

            </main>
        </div>
    );
}
