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
    CheckCircle2
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

// Helper — dynamically resolve API URL (prioritizes Render backend in production/Vercel)
const getApiUrl = (): string => {
    const envUrl = process.env.NEXT_PUBLIC_API_URL;
    if (envUrl && !envUrl.includes('tu-backend.railway.app') && !envUrl.includes('localhost')) {
        return envUrl.replace(/\/$/, '');
    }
    if (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
        return 'https://wetland-monitor-chile.onrender.com';
    }
    return (envUrl || 'http://localhost:8000').replace(/\/$/, '');
};

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

const GlassPanel = ({ children, className = "", id }: { children?: React.ReactNode, className?: string, id?: string }) => (
    <div id={id} className={`bg-gray-900/60 backdrop-blur-xl border border-white/10 rounded-2xl ${className}`}>
        {children}
    </div>
);

// Helper — calculate optimal balanced zoom to fit bounds [minX, minY, maxX, maxY] with generous breathing room
const getOptimalZoom = (
    bounds: [number, number, number, number],
    mapWidth = 420,
    mapHeight = 280,
    paddingX = 110,
    paddingY = 85
): number => {
    const [minX, minY, maxX, maxY] = bounds;
    const lonDiff = Math.max(0.0001, Math.abs(maxX - minX));
    const latDiff = Math.max(0.0001, Math.abs(maxY - minY));

    const adjustedWidth = Math.max(60, mapWidth - paddingX * 2);
    const adjustedHeight = Math.max(50, mapHeight - paddingY * 2);

    // Mercator projection math
    const lat1 = (minY * Math.PI) / 180;
    const lat2 = (maxY * Math.PI) / 180;
    const merc1 = Math.log(Math.tan(Math.PI / 4 + lat1 / 2));
    const merc2 = Math.log(Math.tan(Math.PI / 4 + lat2 / 2));
    const yDiff = Math.max(0.0001, Math.abs(merc2 - merc1));

    const zoomX = Math.log2((adjustedWidth / 256) * (360 / lonDiff));
    const zoomY = Math.log2((adjustedHeight / 256) * ((2 * Math.PI) / yDiff));

    // Balanced zoom: leaves ~50% context around the wetland so it does not feel giant or touch UI borders
    const optimalZoom = Math.min(zoomX, zoomY) - 0.35;
    return Math.max(4, Math.min(15.5, Number(optimalZoom.toFixed(1))));
};

// Helper — extract bounding box [minX, minY, maxX, maxY] from any GeoJSON geometry
const getBoundsFromGeometry = (geometry: any): [number, number, number, number] | null => {
    if (!geometry) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    const extractCoords = (coords: any) => {
        if (!Array.isArray(coords)) return;
        if (coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
            const [x, y] = coords;
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        } else {
            coords.forEach(extractCoords);
        }
    };

    if (geometry.coordinates) {
        extractCoords(geometry.coordinates);
    }
    if (minX !== Infinity && maxX !== -Infinity && minY !== Infinity && maxY !== -Infinity) {
        return [minX, minY, maxX, maxY];
    }
    return null;
};

// Helper — resample sorted daily points to monthly averages
const resampleToMonthly = (dailyData: any[]): any[] => {
    const months: Record<string, { points: any[]; keys: Set<string> }> = {};
    dailyData.forEach(pt => {
        const month = pt.date.substring(0, 7); // YYYY-MM
        if (!months[month]) months[month] = { points: [], keys: new Set() };
        months[month].points.push(pt);
        Object.keys(pt).forEach(k => k !== 'date' && months[month].keys.add(k));
    });

    return Object.entries(months)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([month, { points, keys }]) => {
            const agg: any = { date: month };
            keys.forEach(key => {
                if (key.endsWith('_outlier')) {
                    agg[key] = points.some(p => p[key]);
                } else {
                    const vals = points.map(p => p[key]).filter(v => v !== null && v !== undefined && !isNaN(v));
                    agg[key] = vals.length > 0 ? Number((vals.reduce((s, v) => s + v, 0) / vals.length).toFixed(3)) : null;
                }
            });
            return agg;
        });
};

const METRIC_NAMES: Record<string, string> = {
    Hydrology: 'MNDWI (Hidrología)',
    Vegetation: 'NDRE (Vegetación)',
    WaterQuality: 'NDCI (Calidad Agua)',
    SoilVegetation: 'SAVI (Veg./Suelo)',
    AlgaeBloom: 'FAI (Algas)',
    WaterRatio: 'WRI (Ratio Agua)'
};

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
        const hydroPoint = results['Hydrology']?.time_series.find(p => p.date === date);
        point.Hydrology = hydroPoint != null && typeof hydroPoint.value === 'number' ? Number(hydroPoint.value.toFixed(3)) : null;
        point.Hydrology_outlier = hydroPoint?.is_outlier || false;

        // Vegetation (NDRE)
        const vegPoint = results['Vegetation']?.time_series.find(p => p.date === date);
        point.Vegetation = vegPoint != null && typeof vegPoint.value === 'number' ? Number(vegPoint.value.toFixed(3)) : null;
        point.Vegetation_outlier = vegPoint?.is_outlier || false;

        // WaterQuality (NDCI)
        const qualPoint = results['WaterQuality']?.time_series.find(p => p.date === date);
        point.WaterQuality = qualPoint != null && typeof qualPoint.value === 'number' ? Number(qualPoint.value.toFixed(3)) : null;
        point.WaterQuality_outlier = qualPoint?.is_outlier || false;

        // SoilVegetation (SAVI)
        const saviPoint = results['SoilVegetation']?.time_series.find(p => p.date === date);
        point.SoilVegetation = saviPoint != null && typeof saviPoint.value === 'number' ? Number(saviPoint.value.toFixed(3)) : null;
        point.SoilVegetation_outlier = saviPoint?.is_outlier || false;

        // AlgaeBloom (FAI)
        const faiPoint = results['AlgaeBloom']?.time_series.find(p => p.date === date);
        point.AlgaeBloom = faiPoint != null && typeof faiPoint.value === 'number' ? Number(faiPoint.value.toFixed(3)) : null;
        point.AlgaeBloom_outlier = faiPoint?.is_outlier || false;

        // WaterRatio (WRI)
        const wriPoint = results['WaterRatio']?.time_series.find(p => p.date === date);
        point.WaterRatio = wriPoint != null && typeof wriPoint.value === 'number' ? Number(wriPoint.value.toFixed(3)) : null;
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
    key?: string | number;
    mode: any;
    res: ModeResult | undefined;
    legend: any;
    viewState: any;
    onMove: (evt: any) => void;
    viewYear: 'start' | 'end';
    boundaryGeometry?: any;
}

const IndexCard = ({ mode, res, legend, viewState, onMove, viewYear, boundaryGeometry }: IndexCardProps) => {

    // Memoize the GeoJSON Feature for the wetland perimeter
    const boundaryGeoJson = React.useMemo(() => {
        if (!boundaryGeometry) return null;
        return {
            type: 'Feature' as const,
            geometry: boundaryGeometry,
            properties: {}
        };
    }, [boundaryGeometry]);

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
            {/* COMPACT HEADER */}
            <div className="p-2.5 z-10 bg-gradient-to-b from-black/85 via-black/40 to-transparent flex flex-col gap-0.5 pointer-events-none">
                <div className="flex items-center justify-between w-full">
                    <div className="flex items-center gap-1.5">
                        <mode.icon className={`w-3.5 h-3.5 ${mode.color}`} />
                        <span className="text-xs font-bold tracking-wider text-gray-200">
                            {mode.title} <span className="text-gray-400 font-normal text-[10px]">({mode.acronym})</span>
                        </span>
                    </div>

                    {/* Absolute Median Change (Delta) badge top-right */}
                    {res?.stats?.trend != null && (
                        <span className={`text-[10px] font-mono px-2 py-0.5 rounded-md backdrop-blur-md border font-bold shadow-sm ${
                            res.stats.trend >= 0
                                ? 'bg-green-500/15 text-green-400 border-green-500/30'
                                : 'bg-red-500/15 text-red-400 border-red-500/30'
                        }`} title={`Cambio absoluto interanual (Δ mediana): ${res.stats.trend >= 0 ? '+' : ''}${res.stats.trend.toFixed(3)}`}>
                            Δ {res.stats.trend >= 0 ? '+' : ''}{res.stats.trend.toFixed(3)}
                        </span>
                    )}
                </div>

                <div className="flex items-baseline gap-2.5 mt-0.5">
                    <span className="text-2xl font-mono font-bold text-white drop-shadow-md">
                        {res?.stats?.current != null ? res.stats.current.toFixed(3) : '---'}
                    </span>

                    {/* Robust Statistics Inline */}
                    {res?.stats && (
                        <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
                            {res.stats.current_std != null && (
                                <span><span className="text-gray-500">σ:</span> ±{res.stats.current_std.toFixed(3)}</span>
                            )}
                            {res.stats.cv != null && (
                                <span><span className="text-gray-500">CV:</span> {res.stats.cv.toFixed(1)}%</span>
                            )}
                            {res.stats.outlier_count != null && res.stats.outlier_count > 0 && (
                                <span className="text-yellow-400 bg-yellow-400/10 px-1.5 py-0.2 rounded-full border border-yellow-400/20 text-[9px]">
                                    ⚠ {res.stats.outlier_count}
                                </span>
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
                        <React.Fragment key={`${mode.id}-${viewYear}`}>
                            <Source
                                key={`src-rgb-${mode.id}-${viewYear}-${rgbTile}`}
                                id={`${mode.id}-rgb`}
                                type="raster"
                                tiles={[rgbTile]}
                                tileSize={256}
                            >
                                <Layer
                                    id={`${mode.id}-rgb-layer`}
                                    type="raster"
                                    paint={{ 'raster-opacity': 0.6 }}
                                />
                            </Source>
                            <Source
                                key={`src-metric-${mode.id}-${viewYear}-${metricTile}`}
                                id={`${mode.id}-metric`}
                                type="raster"
                                tiles={[metricTile]}
                                tileSize={256}
                            >
                                <Layer
                                    id={`${mode.id}-metric-layer`}
                                    type="raster"
                                    paint={{}}
                                />
                            </Source>
                        </React.Fragment>
                    )}
                    {/* WETLAND BOUNDARY VECTOR OVERLAY */}
                    {boundaryGeoJson && (
                        <Source
                            key={`boundary-src-${mode.id}`}
                            id={`boundary-src-${mode.id}`}
                            type="geojson"
                            data={boundaryGeoJson}
                        >
                            <Layer
                                id={`boundary-fill-${mode.id}`}
                                type="fill"
                                paint={{
                                    'fill-color': '#0ea5e9',
                                    'fill-opacity': 0.08
                                }}
                            />
                            <Layer
                                id={`boundary-line-halo-${mode.id}`}
                                type="line"
                                paint={{
                                    'line-color': '#000000',
                                    'line-width': 4,
                                    'line-opacity': 0.75
                                }}
                            />
                            <Layer
                                id={`boundary-line-${mode.id}`}
                                type="line"
                                paint={{
                                    'line-color': '#38bdf8',
                                    'line-width': 2.2,
                                    'line-opacity': 1.0
                                }}
                            />
                        </Source>
                    )}
                </Map>
            </div>

            {/* LEGEND OVERLAY */}
            <div className="absolute bottom-2 left-2 right-2 z-10 pointer-events-none">
                <GlassPanel className="p-1.5 backdrop-blur-md bg-black/60 !rounded-lg border-white/5">
                    <div className="flex justify-between text-[8px] text-gray-300 uppercase font-bold mb-0.5">
                        <span>{legend.labels[0]}</span>
                        <span>{legend.title}</span>
                        <span>{legend.labels[2]}</span>
                    </div>
                    <div className="h-1.5 w-full rounded-full mb-0.5" style={{ background: legend.gradient }} />
                    <div className="flex justify-between text-[7px] text-gray-400 font-medium">
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
    const [generatingReport, setGeneratingReport] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Auth (Simplified for CDSE backend)
    const [isApiReady, setIsApiReady] = useState(true);

    // Analysis
    const [startDate, setStartDate] = useState('2016-03-01');
    const [endDate, setEndDate] = useState('2026-01-22');
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

    // CDSE Credentials
    const [cdseUsername, setCdseUsername] = useState('');
    const [cdsePassword, setCdsePassword] = useState('');
    const [cdseConfigured, setCdseConfigured] = useState(false);
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

    // --- EFFECTS ---
    useEffect(() => {
        fetch('/wetlands.json')
            .then(res => res.json())
            .then(setWetlands)
            .catch(console.error);

        // Check CDSE credentials status
        const API = getApiUrl();
        axios.get(`${API}/credentials-status`)
            .then(r => {
                setCdseConfigured(r.data.configured);
                if (!r.data.configured) {
                    setShowCredentials(true);
                }
            })
            .catch(() => {
                setCdseConfigured(false);
                setShowCredentials(true);
            });
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

        if (wetland.bbox) {
            const [minX, minY, maxX, maxY] = wetland.bbox;
            const optimalZoom = getOptimalZoom([minX, minY, maxX, maxY]);
            setViewState({
                longitude: (minX + maxX) / 2,
                latitude: (minY + maxY) / 2,
                zoom: optimalZoom
            });
        }
    };

    const saveSettings = (e: React.FormEvent) => {
        e.preventDefault();
    };

    const handleLogin = () => { };

    const handleVerifyAccess = async () => {
        if (!cdseUsername || !cdsePassword) {
            setCdseError('Ingresa tu email y contraseña de Copernicus para verificar.');
            setCdseSuccess(null);
            return;
        }
        setVerifyingCreds(true);
        setCdseError(null);
        setCdseSuccess(null);
        const API = getApiUrl();
        try {
            const res = await axios.post(`${API}/verify-credentials`, {
                username: cdseUsername,
                password: cdsePassword
            });
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
        try {
            await axios.post(`${API}/set-credentials`, {
                username: cdseUsername,
                password: cdsePassword
            });
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
            setShowCredentials(true);
            setError("Debes ingresar y verificar tus credenciales de Copernicus CDSE antes de iniciar el análisis.");
            return;
        }

        const hasArea = areaSource === 'wetland' ? !!selectedWetland : !!customArea;
        if (!hasArea) {
            setError("Selecciona un humedal o carga un área personalizada.");
            return;
        }

        setLoading(true);
        setError(null);
        setResults(null);
        setProcessLog(["⚙️  Iniciando análisis multi-espectral..."]);
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

        const payload = {
            geojson: { type: "Feature", geometry: aoiGeometry },
            startDate,
            endDate,
            wetlandName: selectedWetland?.name || null
        };

        try {
            setProcessLog(prev => [...prev, "⚙️  Procesando todos los índices con CDSE..."]);
            // Use analyze-all endpoint
            const res = await axios.post(`${getApiUrl()}/analyze-all`, payload);

            if (res.data.status === 'success') {
                const data = res.data.data;
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
            setGeneratingReport(true); // Show report generation indicator
            setProcessLog(prev => [...prev, "📄 Generando reporte (esto puede tardar unos segundos)..."]);

            const bbox = selectedWetland.bbox;
            const centerLat = ((bbox[1] + bbox[3]) / 2).toFixed(4);
            const centerLon = ((bbox[0] + bbox[2]) / 2).toFixed(4);

            const reportPayload = {
                wetland_name: selectedWetland.name,
                wetland_metadata: {
                    region: selectedWetland.region,
                    code: selectedWetland.code || 'N/A',
                    coordinates: `${centerLat}, ${centerLon}`,
                    geometry: selectedWetland.geometry || customArea || null
                },
                analysis_results: results,
                start_date: startDate,
                end_date: endDate
            };

            const res = await axios.post(`${getApiUrl()}/generate-report`, reportPayload, {
                responseType: 'blob'
            });

            // Download file
            const url = window.URL.createObjectURL(new Blob([res.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `Reporte_${selectedWetland.name.replace(/\s+/g, '_')}_${endDate}.docx`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);

            setProcessLog(prev => [...prev, "✓  Reporte descargado exitosamente"]);
        } catch (err) {
            console.error(err);
            alert('Error al generar el reporte. Revisa la consola para más detalles.');
            setProcessLog(prev => [...prev, "✗  Error al generar reporte"]);
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
                            {cdseConfigured ? 'CDSE ACTIVO' : 'CDSE NO CONFIGURADO'}
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
                                    disabled={verifyingCreds || savingCreds || !cdseUsername || !cdsePassword}
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
                                    disabled={savingCreds || verifyingCreds || !cdseUsername || !cdsePassword}
                                    className="flex-1 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-800 disabled:text-gray-500 text-[10px] py-2 rounded-lg font-bold transition-all text-white flex items-center justify-center gap-1.5 shadow-[0_0_15px_rgba(37,99,235,0.25)]"
                                >
                                    {savingCreds ? (
                                        <><div className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" /> Conectando...</>
                                    ) : (
                                        'Conectar CDSE'
                                    )}
                                </button>
                            </div>
                            <p className="text-[8px] text-gray-500 leading-relaxed">
                                Credenciales de tu cuenta de <span className="text-blue-400 underline">dataspace.copernicus.eu</span> para ingesta Sentinel-2.
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
                        <div className="relative">
                            <Search className="absolute left-3 top-2.5 w-4 h-4 text-gray-500 pointer-events-none" />
                            <input
                                ref={searchInputRef}
                                type="text"
                                value={searchQuery}
                                onChange={(e) => { setSearchQuery(e.target.value); updateDropdownRect(); }}
                                onFocus={() => { updateDropdownRect(); if (filteredWetlands.length > 0) setShowSuggestions(true); }}
                                onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
                                placeholder="Buscar humedal..."
                                className="w-full bg-gray-900 border border-white/10 rounded-xl py-2 pl-10 pr-4 text-xs focus:ring-1 focus:ring-blue-500 outline-none"
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
                                    className="bg-gray-950 border border-white/15 rounded-xl max-h-56 overflow-y-auto shadow-2xl"
                                >
                                    {filteredWetlands.map(w => (
                                        <button
                                            key={w.id}
                                            onMouseDown={() => selectWetland(w)}
                                            className="w-full text-left px-4 py-2 text-[10px] hover:bg-white/10 border-b border-white/5 text-gray-300 last:border-0"
                                        >
                                            {w.name}
                                        </button>
                                    ))}
                                </div>,
                                document.body
                            )}
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

                {/* OLD SEARCH (removed, now inside tab) */}
                <div className="relative z-50 hidden">
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

                {/* 3. CONTROL PANEL */}
                <GlassPanel id="control-panel" className="p-4 flex flex-col gap-3 mt-auto">
                    <label className="text-[10px] font-bold text-gray-500 uppercase tracking-widest">Control de Misión</label>
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

                    <button
                        type="button"
                        onClick={handleAnalyze}
                        disabled={loading || generatingReport || !selectedWetland}
                        className={`w-full py-3 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${loading || !selectedWetland ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-blue-600 hover:bg-blue-500 text-white shadow-[0_0_15px_rgba(37,99,235,0.4)]'}`}
                        title={!selectedWetland ? 'Selecciona un humedal primero' : ''}
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
                        disabled={generatingReport || loading || !results || !selectedWetland}
                        className={`w-full py-2.5 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition-all
                            ${generatingReport || !results || !selectedWetland ? 'bg-gray-800 text-gray-500 cursor-not-allowed' : 'bg-purple-600 hover:bg-purple-500 text-white shadow-[0_0_15px_rgba(168,85,247,0.4)]'}`}
                        title={!results ? 'Ejecuta un análisis primero' : 'Descargar reporte Word'}
                    >
                        {generatingReport ? (
                            <><div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" /> GENERANDO REPORTE...</>
                        ) : (
                            <>
                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                                </svg>
                                DESCARGAR REPORTE
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
                            onMove={evt => setViewState(evt.viewState)}
                            viewYear={viewYear}
                            boundaryGeometry={activeGeometry}
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

            </main>
        </div>
    );
}
