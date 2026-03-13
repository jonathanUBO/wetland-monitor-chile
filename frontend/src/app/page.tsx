'use client';

import React, { useState, useEffect } from 'react';
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
    Upload,
    Trash2
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


// --- TYPES ---
interface TimeSeriesPoint {
    date: string;
    value: number;
    metric_name: string;
    is_outlier?: boolean;  // Flag for outlier detection
}

interface ModeResult {
    mode: string;
    stats: {
        current: number;
        current_mean?: number;
        current_std?: number;
        current_min?: number;
        current_max?: number;
        last: number;
        trend: number;
        outlier_count?: number;
        data_count?: number;
        cv?: number;  // Coefficient of variation
    };
    time_series: TimeSeriesPoint[];
    maps: {
        rgb: string;
        metric: string;
        start_year?: { rgb: string; metric: string };
        end_year?: { rgb: string; metric: string };
    };
    coverage?: {
        valid: boolean;
        coverage_days: number;
        data_points: number;
        start_date: string;
        end_date: string;
        reason?: string;
    };
}

const GlassPanel = ({ children, className = "", id }: { children: React.ReactNode, className?: string, id?: string }) => (
    <div id={id} className={`bg-gray-900/60 backdrop-blur-xl border border-white/10 rounded-2xl ${className}`}>
        {children}
    </div>
);

// Helper for merging time series
const mergeTimeSeries = (results: Record<string, ModeResult>) => {
    // Collect all dates
    const allDates = new Set<string>();
    Object.values(results).forEach(r => {
        if (r && r.time_series) {
            r.time_series.forEach(p => allDates.add(p.date));
        }
    });

    const sortedDates = Array.from(allDates).sort();

    return sortedDates.map(date => {
        const point: any = { date };

        // Hydrology (MNDWI)
        const hydroPoint = results['Hydrology']?.time_series?.find((p: any) => p.date === date);
        point.Hydrology = hydroPoint ? hydroPoint.value : null;
        point.Hydrology_outlier = hydroPoint?.is_outlier || false;

        // Vegetation (NDRE)
        const vegPoint = results['Vegetation']?.time_series?.find((p: any) => p.date === date);
        point.Vegetation = vegPoint ? vegPoint.value : null;
        point.Vegetation_outlier = vegPoint?.is_outlier || false;

        // WaterQuality (NDCI)
        const qualPoint = results['WaterQuality']?.time_series?.find((p: any) => p.date === date);
        point.WaterQuality = qualPoint ? qualPoint.value : null;
        point.WaterQuality_outlier = qualPoint?.is_outlier || false;

        // SoilVegetation (SAVI)
        const saviPoint = results['SoilVegetation']?.time_series?.find((p: any) => p.date === date);
        point.SoilVegetation = saviPoint ? saviPoint.value : null;
        point.SoilVegetation_outlier = saviPoint?.is_outlier || false;

        // AlgaeBloom (FAI)
        const faiPoint = results['AlgaeBloom']?.time_series?.find((p: any) => p.date === date);
        point.AlgaeBloom = faiPoint ? faiPoint.value : null;
        point.AlgaeBloom_outlier = faiPoint?.is_outlier || false;

        // WaterRatio (WRI)
        const wriPoint = results['WaterRatio']?.time_series?.find((p: any) => p.date === date);
        point.WaterRatio = wriPoint ? wriPoint.value : null;
        point.WaterRatio_outlier = wriPoint?.is_outlier || false;

        return point;
    });
};

const LEGENDS: any = {
    Hydrology: {
        gradient: 'linear-gradient(to right, red, white, blue)',
        labels: ['-1', '0', '+1'],
        descriptions: ['Seco/Tierra', 'Humedad', 'Agua Profunda'],
        title: 'MNDWI (Humedad)'
    },
    Vegetation: {
        gradient: 'linear-gradient(to right, red, yellow, green)',
        labels: ['0', '0.4', '0.8'],
        descriptions: ['Suelo Desnudo', 'Veg. Baja', 'Veg. Densa'],
        title: 'NDRE (Clorofila)'
    },
    WaterQuality: {
        gradient: 'linear-gradient(to right, blue, cyan, lime, yellow, red)',
        labels: ['-0.1', '0.2', '0.5'],
        descriptions: ['Baja Turb.', 'Media', 'Alta Turb.'],
        title: 'NDCI (Turbidez)'
    },
    SoilVegetation: {
        gradient: 'linear-gradient(to right, #8B4513, #FFD700, #90EE90, #006400)',
        labels: ['-0.5', '0', '0.8'],
        descriptions: ['Suelo', 'Veg. Dispersa', 'Veg. Densa'],
        title: 'SAVI (Veg./Suelo)'
    },
    AlgaeBloom: {
        gradient: 'linear-gradient(to right, #0000FF, #00FFFF, #FFFF00, #FF0000)',
        labels: ['-0.1', '0.2', '0.5'],
        descriptions: ['Sin Algas', 'Presencia', 'Bloom'],
        title: 'FAI (Algas)'
    },
    WaterRatio: {
        gradient: 'linear-gradient(to right, #FF0000, #FFA500, #FFFFFF, #00FFFF, #0000FF)',
        labels: ['-1', '0', '+1'],
        descriptions: ['Tierra (<0.1)', 'Mixto (1.0)', 'Agua (>10)'],
        title: 'WRI (Log Scan)'
    }
};

interface IndexCardProps {
    mode: any;
    res: ModeResult | undefined;
    legend: any;
    viewState: any;
    onMove: (evt: any) => void;
    viewYear: 'start' | 'end';
    selectedWetland?: any;
}

const IndexCard = ({ mode, res, legend, viewState, onMove, viewYear, selectedWetland }: IndexCardProps) => {

    // Determine which map tiles to use
    let rgbTile = res?.maps?.rgb;
    let metricTile = res?.maps?.metric;

    // If backend provided specific year maps
    if (viewYear === 'start' && res?.maps?.start_year) {
        rgbTile = res.maps.start_year.rgb;
        metricTile = res.maps.start_year.metric;
    } else if (viewYear === 'end' && res?.maps?.end_year) {
        rgbTile = res.maps.end_year.rgb;
        metricTile = res.maps.end_year.metric;
    }

    return (
        <div key={mode.id} className={`bg-black/40 border ${mode.border} rounded-2xl flex flex-col relative overflow-hidden group hover:border-white/20 transition-all`}>
            {/* HEADER */}
            <div className="p-3 z-10 bg-gradient-to-b from-black/90 to-transparent flex justify-between items-start pointer-events-none">
                <div className="flex flex-col gap-1 w-full relative">
                    <div className="flex items-center justify-between w-full">
                        <div className="flex items-center gap-2 mb-1">
                            <mode.icon className={`w-4 h-4 ${mode.color}`} />
                            <span className="text-sm font-bold tracking-widest text-gray-300">
                                {mode.title} <span className="text-gray-400 font-normal">({mode.acronym})</span>
                            </span>
                        </div>
                        {/* CONTROLS REMOVED */}
                    </div>

                    <div className="text-3xl font-mono font-medium text-white drop-shadow-md mt-1 flex items-center justify-between">
                        <span>
                            {res?.stats?.trend != null ? 
                                `${res.stats.trend > 0 ? '+' : ''}${res.stats.trend.toFixed(1)}%` : 
                                '0.0%'
                            }
                        </span>
                        <div className={`text-xs font-mono px-2 py-1 rounded-lg backdrop-blur-md border border-white/10 text-gray-400 font-normal`}>
                             {res?.stats?.current != null ? res.stats.current.toFixed(4) : '---'}
                        </div>
                    </div>

                    {/* Robust Statistics */}
                    {res?.stats?.current_std != null && (
                        <div className="flex flex-col gap-1 mt-1 pl-1">
                            <div className="text-xs text-gray-400 font-mono flex items-center gap-1">
                                <span className="text-gray-500">σ:</span> ±{res.stats.current_std.toFixed(4)}
                            </div>
                            {res.stats.cv != null && (
                                <div className="text-xs text-gray-400 font-mono flex items-center gap-1">
                                    <span className="text-gray-500">CV:</span> {res.stats.cv.toFixed(1)}%
                                </div>
                            )}
                            {res.stats.outlier_count != null && res.stats.outlier_count > 0 && (
                                <div className="text-xs text-yellow-400 font-mono flex items-center gap-1 bg-yellow-400/10 px-1.5 py-0.5 rounded-full w-fit border border-yellow-400/20">
                                    ⚠ {res.stats.outlier_count} outliers
                                </div>
                            )}
                        </div>
                    )}
                </div>

            </div>

            {/* MAP BACKGROUND */}
            <div className="absolute inset-0 z-0">
                <Map
                    {...viewState}
                    onMove={onMove}
                    mapLib={maplibregl as any}
                    style={{ width: '100%', height: '100%' }}
                    mapStyle="https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
                    attributionControl={false}
                    reuseMaps={true}
                >
                    {res && rgbTile && metricTile && (
                        <>
                            <Source id={`${mode.id}-rgb-${viewYear}`} type="raster" tiles={[rgbTile]} tileSize={256}>
                                <Layer id={`${mode.id}-rgb-layer-${viewYear}`} type="raster" paint={{ 'raster-opacity': 0.6 }} />
                            </Source>
                            <Source id={`${mode.id}-metric-${viewYear}`} type="raster" tiles={[metricTile]} tileSize={256}>
                                <Layer id={`${mode.id}-metric-layer-${viewYear}`} type="raster" paint={{}} />
                            </Source>
                        </>
                    )}
                    {selectedWetland?.geometry && (
                        <Source id={`${mode.id}-geometry`} type="geojson" data={{ type: "Feature", geometry: selectedWetland.geometry, properties: {} }}>
                            <Layer
                                id={`${mode.id}-geometry-layer`}
                                type="line"
                                paint={{
                                    'line-color': '#fbbf24',
                                    'line-width': 3,
                                    'line-dasharray': [2, 2]
                                }}
                            />
                        </Source>
                    )}
                </Map>
            </div>

            {/* LEGEND OVERLAY */}
            <div className="absolute bottom-2 left-2 right-2 z-10 pointer-events-none">
                <GlassPanel className="p-1.5 backdrop-blur-md bg-black/60 !rounded-lg border-white/5">
                    <div className="flex justify-between text-[10px] text-gray-200 uppercase font-bold mb-0.5">
                        <span>{legend.labels[0]}</span>
                        <span>{legend.title}</span>
                        <span>{legend.labels[2]}</span>
                    </div>
                    <div className="h-2 w-full rounded-full mb-0.5" style={{ background: legend.gradient }} />
                    <div className="flex justify-between text-[9px] text-gray-400 font-medium">
                        <span>{legend.descriptions[0]}</span>
                        <span>{legend.descriptions[1]}</span>
                        <span>{legend.descriptions[2]}</span>
                    </div>
                </GlassPanel>
            </div>

            {!res && <div className="absolute inset-0 flex items-center justify-center bg-black/60 z-20 text-[9px] text-gray-500 uppercase tracking-widest backdrop-blur-sm">Esperando Datos</div>}
        </div>
    );
};

export default function Dashboard() {
    // --- STATE ---
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Auth
    const [accessToken, setAccessToken] = useState<string | null>(null);
    const [clientId, setClientId] = useState('');
    const [projectId, setProjectId] = useState('');
    const [showSettings, setShowSettings] = useState(false);
    const [scriptsLoaded, setScriptsLoaded] = useState(false);

    // Analysis
    const [startDate, setStartDate] = useState('2016-03-01');
    const [endDate, setEndDate] = useState(new Date().toISOString().split('T')[0]);
    const [results, setResults] = useState<Record<string, ModeResult> | null>(null);
    const [mergedData, setMergedData] = useState<any[]>([]);
    const [processLog, setProcessLog] = useState<string[]>([]);

    // Global View State (Start vs End Year)
    const [viewYear, setViewYear] = useState<'start' | 'end'>('end');

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
    const [selectedWetland, setSelectedWetland] = useState<any | null>(null);
    const [showSuggestions, setShowSuggestions] = useState(false);

    // Custom Geometry
    const [customGeometry, setCustomGeometry] = useState<any | null>(null);
    const [uploadingFile, setUploadingFile] = useState(false);

    // --- EFFECTS ---
    useEffect(() => {
        const savedClientId = localStorage.getItem('gee_client_id');
        if (savedClientId) setClientId(savedClientId);

        const savedProjectId = localStorage.getItem('gee_project_id');
        if (savedProjectId) setProjectId(savedProjectId);

        fetch('/wetlands.json')
            .then(res => res.json())
            .then(setWetlands)
            .catch(console.error);

        const checkGoogle = setInterval(() => {
            // @ts-ignore
            if (window.google && window.google.accounts) {
                setScriptsLoaded(true);
                clearInterval(checkGoogle);
            }
        }, 500);

        return () => clearInterval(checkGoogle);
    }, []);

    useEffect(() => {
        if (searchQuery.length > 2) {
            const query = searchQuery.toLowerCase();
            const results = wetlands.filter(w =>
                w.name.toLowerCase().includes(query) ||
                (w.code && w.code.toLowerCase().includes(query))
            ).slice(0, 50);
            setFilteredWetlands(results);
            setShowSuggestions(true);
        } else {
            setFilteredWetlands([]);
            setShowSuggestions(false);
        }
    }, [searchQuery, wetlands]);

    // --- ACTIONS ---
    const selectWetland = (wetland: any) => {
        setSelectedWetland(wetland);
        setSearchQuery(wetland.name);
        setShowSuggestions(false); // Auto-close suggestions

        const [minX, minY, maxX, maxY] = wetland.bbox;
        setViewState({
            longitude: (minX + maxX) / 2,
            latitude: (minY + maxY) / 2,
            zoom: 12
        });
        setCustomGeometry(null); // Clear custom geometry when choosing an inventoried wetland
    };

    const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        setUploadingFile(true);
        setError(null);
        setProcessLog(prev => [...prev, `⚙️  Procesando archivo: ${file.name}`]);

        const formData = new FormData();
        formData.append('file', file);

        try {
            const res = await axios.post(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/process-spatial-file`, formData);
            if (res.data.status === 'success') {
                const geojson = res.data.geojson;
                const bbox = res.data.bbox;

                // Create a virtual wetland object for the "main process"
                const virtualWetland = {
                    id: 'custom-' + Date.now(),
                    name: file.name,
                    code: 'CUSTOM',
                    region: 'Carga Local',
                    bbox: bbox,
                    geometry: geojson.geometry, // The geometry object
                };

                setSelectedWetland(virtualWetland);
                setCustomGeometry(geojson); // Keep for visualization layer if needed
                setSearchQuery(file.name);

                const [minX, minY, maxX, maxY] = bbox;
                setViewState({
                    longitude: (minX + maxX) / 2,
                    latitude: (minY + maxY) / 2,
                    zoom: 13
                });
                setProcessLog(prev => [...prev, "✓  Archivo procesado e integrado"]);
            }
        } catch (err: any) {
            console.error("Upload error:", err);
            const detail = err.response?.data?.detail || "Error al procesar archivo espacial";
            setError(detail);
            setProcessLog(prev => [...prev, `✗  ERR: ${detail}`]);
        } finally {
            setUploadingFile(false);
            if (e.target) e.target.value = '';
        }
    };

    const saveSettings = (e: React.FormEvent) => {
        e.preventDefault();
        localStorage.setItem('gee_client_id', clientId);
        localStorage.setItem('gee_project_id', projectId);
        setShowSettings(false);
    };

    const handleLogin = () => {
        if (!scriptsLoaded || !clientId) {
            setShowSettings(true);
            return;
        }
        // @ts-ignore
        const client = google.accounts.oauth2.initTokenClient({
            client_id: clientId,
            scope: 'https://www.googleapis.com/auth/earthengine https://www.googleapis.com/auth/userinfo.email',
            callback: (resp: any) => {
                if (resp.access_token) setAccessToken(resp.access_token);
                else setError(resp.error);
            },
        });
        client.requestAccessToken();
    };



    const handleAnalyze = async () => {
        if (!accessToken || !projectId) {
            setError("Requiere autenticación y Project ID.");
            return;
        }

        setLoading(true);
        setError(null);
        setResults(null);
        setProcessLog(["⚙️  Iniciando análisis multi-espectral..."]);

        // Reset view year to end by default on new analysis
        setViewYear('end');

        let aoiGeometry;
        if (selectedWetland) {
            // Use exact polygon from inventory/upload if available, otherwise fallback to bbox
            if (selectedWetland.geometry) {
                aoiGeometry = selectedWetland.geometry;
            } else if (selectedWetland.bbox) {
                const [minX, minY, maxX, maxY] = selectedWetland.bbox;
                aoiGeometry = { type: "Polygon", coordinates: [[[minX, minY], [maxX, minY], [maxX, maxY], [minX, maxY], [minX, minY]]] };
            }
        }

        if (!aoiGeometry) {
            setError("Selecciona un humedal o sube un archivo (KML/SHP)");
            setLoading(false);
            return;
        }

        const payload = {
            geojson: { type: "Feature", geometry: aoiGeometry, properties: { name: selectedWetland.name } },
            startDate,
            endDate,
            projectId,
        };

        try {
            setProcessLog(prev => [...prev, "⚙️  Procesando todos los índices..."]);
            // Use analyze-all endpoint
            const res = await axios.post(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/analyze-all`, payload, {
                headers: { Authorization: `Bearer ${accessToken}` }
            });

            if (res.data.status === 'success') {
                const data = res.data.data;
                setResults(data);
                setMergedData(mergeTimeSeries(data));
                setProcessLog(prev => [...prev, "✓  Análisis finalizado exitosamente"]);
            }
        } catch (err: any) {
            console.error("Analysis error:", err);
            const detail = err.response?.data?.detail || err.message || "Error desconocido";
            setError(`Error en análisis: ${detail}`);
            setProcessLog(prev => [...prev, `✗  ERR: ${detail.substring(0, 30)}...`]);
        } finally {
            setLoading(false);
        }
    };


    const handleDownloadReport = async () => {
        if (!results || !selectedWetland) {
            alert('Debes ejecutar un análisis primero');
            return;
        }

        try {
            setLoading(true); // Show loading indicator
            setProcessLog(prev => [...prev, "📄 Preparando datos para el reporte..."]);
            const centerLat = viewState?.latitude ? viewState.latitude.toFixed(4) : "0.0000";
            const centerLon = viewState?.longitude ? viewState.longitude.toFixed(4) : "0.0000";

            const reportPayload = {
                wetland_name: selectedWetland.name,
                wetland_metadata: {
                    region: selectedWetland.region || 'N/A',
                    code: selectedWetland.code || 'CUSTOM',
                    coordinates: `${centerLat}, ${centerLon}`
                },
                analysis_results: results,
                start_date: startDate,
                end_date: endDate
            };

            // Use fetch instead of axios to have strict control over response headers and blob handling
            const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/generate-report`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                },
                body: JSON.stringify(reportPayload)
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            // Extract filename from the Content-Disposition header if possible
            let filename = `Reporte_${selectedWetland.name.replace(/\.[^/.]+$/, "").replace(/\s+/g, '_')}_${endDate}.docx`;
            const disposition = response.headers.get('Content-Disposition');
            if (disposition && disposition.indexOf('filename=') !== -1) {
                const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
                const matches = filenameRegex.exec(disposition);
                if (matches != null && matches[1]) {
                    filename = matches[1].replace(/['"]/g, '');
                }
            }

            const blob = await response.blob();
            // Force the specific MIME type for docx
            const secureBlob = new Blob([blob], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
            const url = window.URL.createObjectURL(secureBlob);

            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            link.style.display = 'none';

            // Required for Firefox but also safe for Chrome
            document.body.appendChild(link);
            link.click();

            // Longer delay to strictly ensure the user's OS registers the file saving intent
            setTimeout(() => {
                document.body.removeChild(link);
                window.URL.revokeObjectURL(url);
            }, 2000);

            setProcessLog(prev => [...prev, "✓  Reporte descargado exitosamente"]);
        } catch (err: any) {
            const errMsg = err.message || err.toString();
            alert(`Error interno en el navegador: ${errMsg}`);
            setProcessLog(prev => [...prev, `✗  Error local: ${errMsg.substring(0, 50)}`]);
        } finally {
            setLoading(false);
        }
    };

    // --- RENDER ---
    return (
        <div className="min-h-screen bg-[#050505] text-white flex flex-col md:flex-row overflow-hidden font-sans">

            {/* SIDEBAR */}
            <aside className="w-full md:w-80 h-auto md:h-screen p-6 flex flex-col gap-6 z-20 pointer-events-auto bg-black border-b md:border-b-0 md:border-r border-white/5 overflow-x-hidden overflow-y-auto custom-scrollbar shrink-0">
                <div className="flex items-center gap-3 mb-2">
                    <div className="w-10 h-10 bg-blue-600 rounded-lg flex items-center justify-center shadow-[0_0_20px_rgba(37,99,235,0.5)]">
                        <Activity className="text-white w-6 h-6" />
                    </div>
                    <h1 className="text-xl font-bold tracking-tight">WETLAND<span className="text-blue-500">MONITOR</span></h1>
                </div>

                {/* 1. AUTH */}
                <GlassPanel className="p-4 flex flex-col gap-3">
                    {!accessToken ? (
                        <button
                            onClick={handleLogin}
                            disabled={!scriptsLoaded}
                            className={`
                                relative overflow-hidden
                                w-full py-3 px-4 rounded-xl font-bold text-sm
                                transition-all duration-300 transform
                                ${!scriptsLoaded
                                    ? 'bg-gray-800 text-gray-500 cursor-wait'
                                    : 'bg-gradient-to-r from-blue-600 via-cyan-600 to-blue-600 text-white hover:scale-[1.02] hover:shadow-[0_0_30px_rgba(6,182,212,0.6)] active:scale-[0.98] bg-[length:200%_100%]'
                                }
                                flex items-center justify-center gap-2
                            `}
                            style={scriptsLoaded ? { animation: 'gradient 3s ease infinite' } : {}}
                        >
                            {!scriptsLoaded ? (
                                <>
                                    <div className="w-4 h-4 border-2 border-gray-500/30 border-t-gray-400 rounded-full animate-spin" />
                                    Cargando...
                                </>
                            ) : (
                                <>
                                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                                    </svg>
                                    CONECTAR GEE
                                </>
                            )}
                            {/* Efecto de brillo animado */}
                            {scriptsLoaded && (
                                <div
                                    className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent"
                                    style={{
                                        animation: 'shimmer 2s infinite', transform: 'translateX(-100%)'
                                    }}
                                />
                            )}
                        </button>
                    ) : (
                        <div className="w-full py-3 px-4 text-green-400 text-sm font-bold flex items-center justify-center gap-2 border border-green-500/30 rounded-xl bg-gradient-to-r from-green-900/20 to-emerald-900/20 shadow-[0_0_20px_rgba(16,185,129,0.3)]">
                            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                            </svg>
                            CONECTADO
                            <span className="absolute right-3 w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                        </div>
                    )}
                    <button onClick={() => setShowSettings(!showSettings)} className="text-[10px] text-gray-500 hover:text-white flex items-center gap-1">
                        <Settings className="w-3 h-3" /> Configuración
                    </button>
                    {showSettings && (
                        <div className="space-y-2 animate-in fade-in slide-in-from-top-2">
                            <input value={clientId} onChange={e => setClientId(e.target.value)} placeholder="Client ID" className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px]" />
                            <input value={projectId} onChange={e => setProjectId(e.target.value)} placeholder="Project ID" className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px]" />
                            <button onClick={saveSettings} className="bg-blue-600 text-[10px] px-2 py-1 rounded w-full">Guardar</button>
                        </div>
                    )}
                </GlassPanel>

                {/* 2. SEARCH */}
                <div className="relative z-50">
                    <div className="relative">
                        <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500" />
                        <input
                            type="text"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                            placeholder="Buscar humedal..."
                            className="w-full bg-gray-900 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-xs focus:ring-1 focus:ring-blue-500 outline-none"
                        />
                    </div>
                    {showSuggestions && filteredWetlands.length > 0 && (
                        <div className="absolute top-full left-0 right-0 mt-2 bg-black border border-white/10 rounded-xl max-h-40 overflow-y-auto z-50">
                            {filteredWetlands.map(w => (
                                <button key={w.id} onClick={() => selectWetland(w)} className="w-full text-left px-4 py-2 text-[10px] hover:bg-white/10 border-b border-white/5 text-gray-300">
                                    {w.name}
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* 3. UPLOAD CUSTOM AREA */}
                <GlassPanel className="p-4 flex flex-col gap-2 border-dashed border-white/20 hover:border-blue-500/40 transition-colors">
                    <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <Upload className="w-3 h-3 text-blue-400" /> Nuevo Humedal
                        </div>
                        {customGeometry && (
                            <button
                                onClick={() => { setCustomGeometry(null); setSearchQuery(''); }}
                                className="text-red-400 hover:text-red-300 flex items-center gap-1 transition-colors"
                            >
                                <Trash2 className="w-2.5 h-2.5" /> Limpiar
                            </button>
                        )}
                    </label>
                    <p className="text-[9px] text-gray-500 leading-tight">Carga un archivo KML, KMZ o SHP (zip) para analizar un área personalizada.</p>

                    <div className="relative mt-1">
                        <input
                            type="file"
                            accept=".kml,.kmz,.zip"
                            onChange={handleFileUpload}
                            disabled={uploadingFile}
                            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-wait"
                        />
                        <div className={`
                            w-full py-2.5 px-4 rounded-xl border border-white/10 bg-white/5 
                            flex items-center justify-center gap-2 text-[10px] font-bold tracking-tight
                            ${uploadingFile ? 'text-blue-400 animate-pulse' : 'text-gray-300'}
                        `}>
                            {uploadingFile ? (
                                <>
                                    <div className="w-3 h-3 border-2 border-blue-400/30 border-t-blue-400 rounded-full animate-spin" />
                                    PROCESANDO...
                                </>
                            ) : (
                                <>
                                    <Upload className="w-3.5 h-3.5" />
                                    {customGeometry ? 'ARCHIVO CARGADO' : 'SUBIR ARCHIVO'}
                                </>
                            )}
                        </div>
                    </div>
                </GlassPanel>

                {/* 2.5 WETLAND PROFILE */}
                {
                    selectedWetland && (
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
                    )
                }

                {/* 2.6 NETWORK STATUS (Show when no wetland selected) */}
                {
                    !selectedWetland && (
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
                                    <span className="text-gray-400">Sentinel-1 SAR</span>
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
                    )
                }

                {/* 2.7 TEMPORAL COVERAGE (Show when results available) */}
                {
                    results && results['Hydrology']?.coverage && (
                        <GlassPanel className="p-4 flex flex-col gap-2">
                            <label className="text-[10px] text-gray-400 font-bold uppercase tracking-wider flex items-center gap-2">
                                <Calendar className="w-3 h-3 text-green-400" /> Cobertura Temporal
                            </label>

                            <div className="grid grid-cols-2 gap-2">
                                <div className="bg-white/5 p-2 rounded-lg">
                                    <div className="text-[9px] text-gray-500 uppercase">Días de Datos</div>
                                    <div className="text-sm font-mono font-bold text-green-400">
                                        {results['Hydrology']?.coverage?.coverage_days || 'N/A'}
                                    </div>
                                </div>
                                <div className="bg-white/5 p-2 rounded-lg">
                                    <div className="text-[9px] text-gray-500 uppercase">Puntos Válidos</div>
                                    <div className="text-sm font-mono font-bold text-blue-400">
                                        {results['Hydrology']?.stats?.data_count || 'N/A'}
                                    </div>
                                </div>
                            </div>

                            <div className="bg-white/5 p-2 rounded-lg mt-1">
                                <div className="text-[9px] text-gray-500 uppercase">Período</div>
                                <div className="text-[9px] font-mono text-gray-300">
                                    {results['Hydrology']?.coverage?.start_date || 'N/A'} → {results['Hydrology']?.coverage?.end_date || 'N/A'}
                                </div>
                            </div>

                            {/* Quality indicator */}
                            {(() => {
                                const cv = results['Hydrology']?.stats?.cv || 0;
                                const outliers = results['Hydrology']?.stats?.outlier_count || 0;
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
                    )
                }

                {/* 3. CONTROL PANEL */}
                <GlassPanel id="control-panel" className="p-4 flex flex-col gap-3 mt-auto">
                    <label className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Control de Misión</label>
                    <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                            <label className="text-[10px] text-gray-500">Inicio</label>
                            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px]" />
                        </div>
                        <div className="space-y-1">
                            <label className="text-[10px] text-gray-500">Fin</label>
                            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} className="w-full bg-black border border-white/10 rounded px-2 py-1 text-[10px]" />
                        </div>
                    </div>

                    <button
                        onClick={handleAnalyze}
                        disabled={loading || !accessToken || !selectedWetland}
                        className={`w-full py-3 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${loading || !accessToken || !selectedWetland ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_15px_rgba(37,99,235,0.4)]'}`}
                        title={!selectedWetland ? 'Selecciona un humedal o sube un archivo primero' : !accessToken ? 'Conéctate a GEE primero' : ''}
                    >
                        {loading ? <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> : <Layers className="w-4 h-4" />}
                        EJECUTAR TODO
                    </button>

                    {/* DOWNLOAD REPORT BUTTON */}
                    <button
                        onClick={handleDownloadReport}
                        disabled={!results || !selectedWetland}
                        className={`w-full py-2 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${!results || !selectedWetland ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)]'}`}
                        title={!results ? 'Ejecuta un análisis primero' : 'Descargar reporte Word'}
                    >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                        </svg>
                        DESCARGAR REPORTE
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
            <main className="flex-1 h-auto md:h-screen overflow-y-auto md:overflow-hidden flex flex-col relative bg-gradient-to-br from-gray-900 to-black p-4 gap-4">

                {/* TOP GRID: 6 MAP CARDS */}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 md:h-[70%]">
                    {
                        [
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
                                onMove={evt => setViewState(evt.viewState)}
                                viewYear={viewYear}
                                selectedWetland={selectedWetland}
                            />
                        ))
                    }
                </div>

                {/* BOTTOM CHART - Reduced to 25% */}
                <div className="h-[25%] bg-black/40 border border-white/10 rounded-2xl p-3 flex flex-col min-h-0">
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
                                <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: '#666' }} domain={['auto', 'auto']} />
                                <Tooltip contentStyle={{ backgroundColor: '#000', border: '1px solid #333', borderRadius: '8px' }} />

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

            </main>
        </div>
    );
}
