/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts}",
  ],
  theme: {
    extend: {
      colors: {
        rise: '#ef4444',
        fall: '#22c55e',
        bg: '#0d1117',
        card: '#161b22',
        border: '#30363d',
        accent: '#58a6ff',
        muted: '#8b949e',
      }
    },
  },
  plugins: [],
}
