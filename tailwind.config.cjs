/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/frontend/**/*.{js,jsx,ts,tsx}",
    "./app/views/**/*.html.erb",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "system-ui", "-apple-system", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 3px rgb(0 0 0 / 0.06), 0 1px 2px rgb(0 0 0 / 0.04)",
        popup: "0 10px 40px rgb(0 0 0 / 0.12), 0 2px 8px rgb(0 0 0 / 0.08)",
      },
    },
  },
  plugins: [],
};
