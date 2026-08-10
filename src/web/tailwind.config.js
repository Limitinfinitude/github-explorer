/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: '#0969da', hover: '#0550ae', subtle: 'rgba(9,105,218,0.08)' },
        success: { DEFAULT: '#1a7f37', subtle: 'rgba(26,127,55,0.08)' },
        error:   { DEFAULT: '#cf222e', subtle: 'rgba(207,34,46,0.08)' },
        warning: { DEFAULT: '#9a6700' },
        bg: {
          DEFAULT: 'var(--bg)',
          secondary: 'var(--bg-secondary)',
          elevated: 'var(--bg-elevated)',
          hover: 'var(--bg-hover)',
        },
      },
      fontFamily: {
        mono: ['Cascadia Code', 'Fira Code', 'JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
