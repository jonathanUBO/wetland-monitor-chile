import React from 'react';

// Helper — dynamically resolve API URL (supports Docker/Nginx same-origin, Render in cloud, and local dev)
export const getApiUrl = (): string => {
    const envUrl = process.env.NEXT_PUBLIC_API_URL;
    if (envUrl && !envUrl.includes('tu-backend.railway.app')) {
        return envUrl.replace(/\/$/, '');
    }
    if (typeof window !== 'undefined') {
        const hostname = window.location.hostname;
        const port = window.location.port;
        // Same-origin reverse proxy (e.g. Nginx serving on port 80 or 443)
        if (port === '' || port === '80' || port === '443') {
            if (!hostname.endsWith('.vercel.app')) {
                return '';
            }
        }
        if (hostname.endsWith('.vercel.app') || hostname.endsWith('.render.com')) {
            return 'https://wetland-monitor-chile.onrender.com';
        }
        // Local area network IP (192.168.x.x, 10.x.x.x, 172.16-31.x.x)
        if (/^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)/.test(hostname)) {
            return `http://${hostname}:8000`;
        }
        if (hostname !== 'localhost' && hostname !== '127.0.0.1') {
            return 'https://wetland-monitor-chile.onrender.com';
        }
    }
    return (envUrl || 'http://localhost:8000').replace(/\/$/, '');
};

export const resolveBackendUrl = (url: string | undefined): string => {
    if (!url) return '';
    const api = getApiUrl();
    if (url.startsWith('/')) return api ? `${api}${url}` : url;
    if (url.startsWith('http://localhost:8000') || url.startsWith('http://127.0.0.1:8000')) {
        const path = url.replace(/^https?:\/\/[^\/]+/, '');
        return api ? `${api}${path}` : path;
    }
    return url;
};

// --- CHILEAN ADMINISTRATIVE REGIONS (North to South) ---
export const CHILEAN_REGIONS = [
    "Arica y Parinacota",
    "Tarapacá",
    "Antofagasta",
    "Atacama",
    "Coquimbo",
    "Valparaíso",
    "Metropolitana",
    "O'Higgins",
    "Maule",
    "Ñuble",
    "Biobío",
    "La Araucanía",
    "Los Ríos",
    "Los Lagos",
    "Aysén",
    "Magallanes"
];

// --- TYPES ---
export interface SituationalStatus {
    status_label: string;
    status_level: 'optimal' | 'good' | 'alert' | 'degraded' | 'critical' | 'unknown';
    status_color: string;
    threshold: string;
    scientific_basis: string;
    description: string;
}

export interface ConsolidatedStatus {
    imce_score: number | null;
    weci_score: number | null; // backward compatibility
    score?: number | null;
    health_level: 'optimal' | 'good' | 'alert' | 'degraded' | 'critical' | 'unknown';
    health_label: string;
    health_color: string;
    health_badge: string;
    has_water_body?: boolean;
    dimensions: {
        hydrology: { score: number | null; weight: number; label: string; indicators: string[] };
        vegetation: { score: number | null; weight: number; label: string; indicators: string[] };
        water_quality: { score: number | null; weight: number; label: string; indicators: string[]; status?: string };
    };
    diagnosis: {
        code: string;
        title: string;
        description: string;
        regulatory_verdict: string;
    };
    scientific_framework?: string;
}

export interface TimeSeriesPoint {
    date: string;
    value: number;
    metric_name: string;
    is_outlier?: boolean;  // Flag for outlier detection
}

export interface ModeResult {
    mode: string;
    stats: {
        current: number;
        current_mean?: number;
        current_std?: number;
        current_min?: number;
        current_max?: number;
        initial?: number;
        initial_mean?: number;
        initial_std?: number;
        last: number;
        trend: number;
        trend_delta?: number;
        sen_slope?: number;
        outlier_count?: number;
        data_count?: number;
        cv?: number;  // Coefficient of variation
    };
    situational_status?: SituationalStatus;
    initial_situational_status?: SituationalStatus;
    time_series: TimeSeriesPoint[];
    maps: {
        rgb: string;
        metric: string;
        start_year?: { rgb: string; metric: string; overlay?: any };
        end_year?: { rgb: string; metric: string; overlay?: any };
        overlay?: {
            rgb_url: string;
            metric_url: string;
            coordinates: [[number, number], [number, number], [number, number], [number, number]];
        };
        thumb_url?: string;
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

export const GlassPanel = ({ children, className = "", id }: { children?: React.ReactNode, className?: string, id?: string }) => (
    <div id={id} className={`bg-gray-900/60 backdrop-blur-xl border border-white/10 rounded-2xl ${className}`}>
        {children}
    </div>
);

// Helper — calculate optimal balanced zoom to fit bounds [minX, minY, maxX, maxY] with generous breathing room
export const getOptimalZoom = (
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
export const getBoundsFromGeometry = (geometry: any): [number, number, number, number] | null => {
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

    if (geometry.type === 'FeatureCollection' && Array.isArray(geometry.features)) {
        geometry.features.forEach((f: any) => {
            if (f?.geometry?.coordinates) extractCoords(f.geometry.coordinates);
        });
    } else if (geometry.type === 'Feature' && geometry.geometry?.coordinates) {
        extractCoords(geometry.geometry.coordinates);
    } else if (geometry.coordinates) {
        extractCoords(geometry.coordinates);
    }
    if (minX !== Infinity && maxX !== -Infinity && minY !== Infinity && maxY !== -Infinity) {
        return [minX, minY, maxX, maxY];
    }
    return null;
};

// Helper — resample sorted daily points to robust monthly medians (outlier-aware)
export const resampleToMonthly = (dailyData: any[]): any[] => {
    const months: Record<string, { points: any[]; keys: Set<string> }> = {};
    dailyData.forEach(pt => {
        const month = pt.date.substring(0, 7); // YYYY-MM
        if (!months[month]) months[month] = { points: [], keys: new Set() };
        months[month].points.push(pt);
        Object.keys(pt).forEach(k => k !== 'date' && months[month].keys.add(k));
    });

    const calcMedian = (arr: number[]): number => {
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
    };

    return Object.entries(months)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([month, { points, keys }]) => {
            const agg: any = { date: month };
            keys.forEach(key => {
                if (key.endsWith('_outlier')) {
                    agg[key] = points.some(p => p[key]);
                } else {
                    const outKey = `${key}_outlier`;
                    // Prefer non-outlier observations when computing monthly median so cloud shadows don't corrupt the curve
                    const cleanVals = points
                        .filter(p => !p[outKey])
                        .map(p => p[key])
                        .filter(v => v !== null && v !== undefined && !isNaN(v));
                    const allVals = points
                        .map(p => p[key])
                        .filter(v => v !== null && v !== undefined && !isNaN(v));
                    const targetVals = cleanVals.length > 0 ? cleanVals : allVals;
                    agg[key] = targetVals.length > 0 ? Number(calcMedian(targetVals).toFixed(3)) : null;
                }
            });
            return agg;
        });
};

export const METRIC_NAMES: Record<string, string> = {
    Hydrology: 'MNDWI (Hidrología)',
    Vegetation: 'NDRE (Vegetación)',
    WaterQuality: 'NDCI (Calidad Agua)',
    SoilVegetation: 'SAVI (Veg./Suelo)',
    AlgaeBloom: 'FAI (Algas)',
    WaterRatio: 'WRI (Ratio Agua)'
};

// Helper for merging time series with O(N) hash map indexing
export const mergeTimeSeries = (results: Record<string, ModeResult>) => {
    const allDates = new Set<string>();
    const modeMaps: Record<string, Record<string, any>> = {};
    const modes = ['Hydrology', 'Vegetation', 'WaterQuality', 'SoilVegetation', 'AlgaeBloom', 'WaterRatio'];

    modes.forEach(m => {
        const dict: Record<string, any> = {};
        const series = results[m]?.time_series;
        if (series && Array.isArray(series)) {
            series.forEach(p => {
                if (p && p.date) {
                    allDates.add(p.date);
                    dict[p.date] = p;
                }
            });
        }
        modeMaps[m] = dict;
    });

    const sortedDates = Array.from(allDates).sort();

    return sortedDates.map(date => {
        const point: any = { date };
        modes.forEach(m => {
            const p = modeMaps[m][date];
            point[m] = p != null && typeof p.value === 'number' ? Number(p.value.toFixed(3)) : null;
            point[`${m}_outlier`] = p?.is_outlier || false;
        });
        return point;
    });
};

export const LEGENDS: any = {
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
        gradient: 'linear-gradient(to right, #8B4513, #CE7E45, #FCD163, #90EE90, #22C55E, #006400)',
        labels: ['0.0', '0.40', '0.80+'],
        descriptions: ['Suelo Desnudo (<0.10)', 'Cobertura Moderada', 'Vegetación Densa (>0.45)'],
        title: 'SAVI (Veg./Suelo)'
    },
    AlgaeBloom: {
        gradient: 'linear-gradient(to right, #0000FF, #00FFFF, #00FF00, #FFFF00, #FF0000, #8B0000)',
        labels: ['-0.05', '0.02', '0.20+'],
        descriptions: ['Agua Clara (≤0.0)', 'Incipiente (0.02)', 'Floración Masiva (>0.05)'],
        title: 'FAI (Algas)'
    },
    WaterRatio: {
        gradient: 'linear-gradient(to right, #FF0000, #FFA500, #FFFF00, #FFFFFF, #00FFFF, #0000FF)',
        labels: ['0.0', '1.0', '2.0+'],
        descriptions: ['Tierra (<0.5)', 'Umbral Agua (1.0)', 'Cuerpo de Agua (>1.0)'],
        title: 'WRI (Ratio Agua)'
    }
};

// SATELLITE BASEMAP STYLE (High-Resolution Global Imagery via ESRI)
export const SATELLITE_MAP_STYLE: any = {
    version: 8,
    sources: {
        'esri-satellite': {
            type: 'raster',
            tiles: [
                'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
            ],
            tileSize: 256,
            maxzoom: 19,
            attribution: 'Esri, Maxar, Earthstar Geographics'
        }
    },
    layers: [
        {
            id: 'esri-satellite-layer',
            type: 'raster',
            source: 'esri-satellite',
            minzoom: 0,
            maxzoom: 19
        }
    ]
};
