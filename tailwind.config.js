module.exports = {
  content: [
    "./backend/templates/**/*.html",
    "./backend/apps/**/*.py",
    "./backend/static_src/**/*.css"
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: "#EFF6FF",
          100: "#DBEAFE",
          200: "#BFDBFE",
          300: "#93C5FD",
          400: "#5DA2FF",
          500: "#2563FF",
          600: "#1554F6",
          700: "#0F43C8",
          900: "#0B1B46",
          950: "#081331"
        },
        teal: {
          50: "#E9FFFC",
          100: "#C9FFF7",
          200: "#92F7EF",
          300: "#4FE5DD",
          400: "#00D1C9",
          500: "#00B8C8",
          600: "#0092A4",
          700: "#0A7282"
        },
        graphite: {
          50: "#F7F9FC",
          100: "#F2F4F8",
          200: "#DDE5EE",
          300: "#CBD5E1",
          500: "#6B7280",
          700: "#334155",
          800: "#1E293B",
          900: "#0B1331",
          950: "#07111F"
        },
        coral: {
          50: "#FFF4F4",
          100: "#FFE3E3",
          200: "#FFC9C9",
          400: "#FF7C7C",
          500: "#EF4444",
          600: "#DC2626",
          700: "#B91C1C"
        },
        surface: {
          DEFAULT: "#ffffff",
          50: "#F7F9FC",
          100: "#F2F4F8",
          200: "#DDE5EE"
        }
      },
      fontFamily: {
        sans: ["Poppins", "ui-sans-serif", "system-ui"]
      },
      boxShadow: {
        card: "0 10px 30px rgba(11,19,49,.06), 0 1px 3px rgba(11,19,49,.08)",
        "card-hover": "0 16px 44px rgba(11,19,49,.12)",
        glow: "0 0 0 5px rgba(0,209,201,.12), 0 16px 40px rgba(37,99,255,.18)",
        depth: "0 28px 90px rgba(11,19,49,.15)"
      }
    }
  }
};
