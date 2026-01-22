import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import Script from 'next/script'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
    title: 'Wetland AI | Panel GEOINT',
    description: 'Monitoreo Avanzado de Humedales Multi-Sensor',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="es">
            <head>
                {/* Google Identity Services */}
                <Script
                    src="https://accounts.google.com/gsi/client"
                    strategy="beforeInteractive"
                />
            </head>
            <body className={inter.className}>{children}</body>
        </html>
    )
}
