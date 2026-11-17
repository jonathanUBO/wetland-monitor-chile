'use client';

import React, { useRef, useCallback, useEffect } from 'react';
import Map, { Source, Layer } from 'react-map-gl';
import maplibregl from 'maplibre-gl';
import {
    ModeResult,
    SATELLITE_MAP_STYLE,
    resolveBackendUrl,
    GlassPanel,
} from '../lib/wetland';

interface IndexCardProps {
    key?: string | number;
    mode: any;
    res: ModeResult | undefined;
    legend: any;
    viewState: any;
    onMove: (evt: any) => void;
    viewYear: 'start' | 'end';
    boundaryGeometry?: any;
    mapMode?: 'satellite' | 'dark';
    cdseToken?: string;
}

export const IndexCard = React.memo(({ mode, res, legend, viewState, onMove, viewYear, boundaryGeometry, mapMode = 'satellite', cdseToken }: IndexCardProps) => {

    // Memoize the GeoJSON FeatureCollection for the wetland perimeter
    const boundaryGeoJson = React.useMemo(() => {
        if (!boundaryGeometry) return null;
        return {
            type: 'FeatureCollection' as const,
            features: [
                {
                    type: 'Feature' as const,
                    geometry: boundaryGeometry,
                    properties: {}
                }
            ]
        };
    }, [boundaryGeometry]);

    // Fast Single-Image Overlay (BBOX composite, instant load)
    let overlayData = res?.maps?.overlay;
    let rgbTile = resolveBackendUrl(res?.maps?.rgb);
    let metricTile = resolveBackendUrl(res?.maps?.metric);

    // If backend provided specific year maps
    if (viewYear === 'start' && res?.maps?.start_year) {
        overlayData = res.maps.start_year.overlay || overlayData;
        rgbTile = resolveBackendUrl(res.maps.start_year.rgb);
        metricTile = resolveBackendUrl(res.maps.start_year.metric);
    } else if (viewYear === 'end' && res?.maps?.end_year) {
        overlayData = res.maps.end_year.overlay || overlayData;
        rgbTile = resolveBackendUrl(res.maps.end_year.rgb);
        metricTile = resolveBackendUrl(res.maps.end_year.metric);
    }

    const mapRef = useRef<any>(null);

    // Bring boundary line layers to the absolute top of the MapLibre layer stack
    const bringBoundaryToFront = useCallback(() => {
        try {
            const map = mapRef.current?.getMap ? mapRef.current.getMap() : mapRef.current;
            if (!map || !map.getStyle) return;
            const style = map.getStyle();
            if (!style || !style.layers) return;
            const layers = style.layers;
            const haloId = `boundary-line-halo-${mode.id}`;
            const lineId = `boundary-line-${mode.id}`;
            const lastLayerId = layers[layers.length - 1]?.id;

            // If already the topmost layer, nothing to do
            if (lastLayerId === lineId) return;

            if (map.getLayer(haloId)) {
                map.moveLayer(haloId);
            }
            if (map.getLayer(lineId)) {
                map.moveLayer(lineId);
            }
        } catch {
            // style loading or layer not yet added
        }
    }, [mode.id]);

    useEffect(() => {
        const t1 = setTimeout(bringBoundaryToFront, 100);
        const t2 = setTimeout(bringBoundaryToFront, 400);
        return () => {
            clearTimeout(t1);
            clearTimeout(t2);
        };
    }, [overlayData, rgbTile, metricTile, viewYear, boundaryGeoJson, bringBoundaryToFront]);

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
                        {viewYear === 'start'
                            ? (res?.stats?.initial != null ? res.stats.initial.toFixed(3) : (res?.stats?.current != null ? res.stats.current.toFixed(3) : '---'))
                            : (res?.stats?.current != null ? res.stats.current.toFixed(3) : '---')}
                    </span>

                    {/* Robust Statistics Inline */}
                    {res?.stats && (
                        <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
                            {viewYear === 'start' ? (
                                (res.stats.initial_std != null || res.stats.current_std != null) && (
                                    <span><span className="text-gray-500">σ:</span> ±{(res.stats.initial_std ?? res.stats.current_std)?.toFixed(3)}</span>
                                )
                            ) : (
                                res.stats.current_std != null && (
                                    <span><span className="text-gray-500">σ:</span> ±{res.stats.current_std.toFixed(3)}</span>
                                )
                            )}
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 text-gray-400 uppercase font-semibold">
                                {viewYear === 'start' ? 'Inicial' : 'Final'}
                            </span>
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

                {/* SITUATIONAL STATUS BADGE */}
                {(() => {
                    const statusInfo = (viewYear === 'start' ? res?.initial_situational_status : res?.situational_status) || null;
                    if (!statusInfo) return null;
                    return (
                        <div className="flex items-center gap-1.5 mt-0.5 pointer-events-auto">
                            <span 
                                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold border backdrop-blur-md transition-all shadow-sm"
                                style={{
                                    backgroundColor: `${statusInfo.status_color}18`,
                                    borderColor: `${statusInfo.status_color}50`,
                                    color: statusInfo.status_color
                                }}
                                title={`Criterio: ${statusInfo.threshold} (${statusInfo.scientific_basis}) - ${statusInfo.description}`}
                            >
                                <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ backgroundColor: statusInfo.status_color }} />
                                {statusInfo.status_label}
                            </span>
                            <span className="text-[8.5px] font-mono text-gray-400 truncate max-w-[125px]" title={statusInfo.threshold}>
                                {statusInfo.threshold}
                            </span>
                        </div>
                    );
                })()}
            </div>

            {/* MAP BACKGROUND */}
            <div className="absolute inset-0 z-0">
                <Map
                    ref={mapRef}
                    {...viewState}
                    onMove={onMove}
                    mapLib={maplibregl as any}
                    style={{ width: '100%', height: '100%' }}
                    mapStyle={mapMode === 'dark' ? "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json" : SATELLITE_MAP_STYLE}
                    attributionControl={false}
                    reuseMaps={true}
                    onIdle={bringBoundaryToFront}
                    transformRequest={(url: string) => {
                        if (cdseToken && (url.includes('/api/overlay/') || url.includes('/api/tiles/') || url.includes('/api/thumb/'))) {
                            return {
                                url,
                                headers: { Authorization: `Bearer ${cdseToken}` }
                            };
                        }
                        return { url };
                    }}
                >
                    {/* 1. FAST SINGLE-IMAGE OVERLAYS (INSTANT LOAD) */}
                    {res && overlayData?.coordinates && overlayData.metric_url && (
                        <Source
                            key={`src-ov-metric-${mode.id}-${viewYear}-${resolveBackendUrl(overlayData.metric_url)}`}
                            id={`${mode.id}-ov-metric`}
                            type="image"
                            url={resolveBackendUrl(overlayData.metric_url)}
                            coordinates={overlayData.coordinates}
                        >
                            <Layer
                                id={`${mode.id}-ov-metric-layer`}
                                type="raster"
                                paint={{ 'raster-opacity': 0.85 }}
                            />
                        </Source>
                    )}

                    {/* 2. SLIPPY TILE RASTER LAYERS (FALLBACK OR COMPLEMENT) */}
                    {res && (!overlayData?.coordinates) && rgbTile && metricTile && (
                        <React.Fragment key={`tiles-${mode.id}-${viewYear}`}>
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

                    {/* 3. WETLAND BOUNDARY VECTOR OVERLAY - ALWAYS ON TOP, UNFILLED, CRISP THIN LINE */}
                    {boundaryGeoJson && (
                        <Source
                            key={`boundary-src-${mode.id}-${res ? 'res' : 'no'}-${viewYear}-${overlayData?.rgb_url ? 'ov' : 'tile'}`}
                            id={`boundary-src-${mode.id}`}
                            type="geojson"
                            data={boundaryGeoJson}
                        >
                            <Layer
                                id={`boundary-line-halo-${mode.id}`}
                                type="line"
                                paint={{
                                    'line-color': '#000000',
                                    'line-width': 2.2,
                                    'line-opacity': 0.85
                                }}
                            />
                            <Layer
                                id={`boundary-line-${mode.id}`}
                                type="line"
                                paint={{
                                    'line-color': '#00f5ff',
                                    'line-width': 1.3,
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

            {/* CLEAN STATUS BADGE (DOES NOT OBSTRUCT MAP OR BOUNDARY) */}
            {!res && (
                <div className="absolute top-14 left-2.5 z-10 pointer-events-none flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-black/75 backdrop-blur-md border border-cyan-500/30 text-[9px] font-mono text-cyan-300 shadow-md">
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
                    <span>{boundaryGeometry ? 'Perímetro Delimitado' : 'Esperando Selección'}</span>
                </div>
            )}
        </div>
    );
}, (prev, next) => {
    return (
        prev.mode.id === next.mode.id &&
        prev.res === next.res &&
        prev.legend === next.legend &&
        prev.viewYear === next.viewYear &&
        prev.mapMode === next.mapMode &&
        prev.boundaryGeometry === next.boundaryGeometry &&
        prev.cdseToken === next.cdseToken &&
        prev.viewState?.longitude === next.viewState?.longitude &&
        prev.viewState?.latitude === next.viewState?.latitude &&
        prev.viewState?.zoom === next.viewState?.zoom
    );
});
