/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/frontend/**/*.{js,jsx,ts,tsx}",
    "./app/views/**/*.html.erb",
  ],
  theme: {
    extend: {
      colors: {
        rc: {
          bg: "#f3f7fd",
          text: "#0f172a",
          muted: "#475569",
          line: "#d9e3f0",
          card: "#ffffff",
          cardSoft: "#f8fbff",
          primary: "#0f766e",
          primaryDark: "#0c615a",
        },
      },
      boxShadow: {
        soft: "0 14px 30px rgb(15 23 42 / 0.08)",
      },
    },
  },
  plugins: [],
};
