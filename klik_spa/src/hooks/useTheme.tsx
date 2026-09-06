"use client"

import { createContext, useContext, useState, useEffect, type ReactNode } from "react"

interface ThemeContextType {
  theme: "light" | "dark"
  setTheme: (theme: "light" | "dark") => void
  toggleTheme: () => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Default theme for the KLiK POS profile is Dark, not Light -- this is
  // hardcoded (not derived from the OS/browser's prefers-color-scheme) so a
  // fresh install or a browser with no saved preference yet always opens in
  // Dark. A cashier who explicitly switches to Light via the theme toggle
  // still has that choice remembered via localStorage on their next visit --
  // this only changes what a user who has never toggled it sees.
  const [theme, setTheme] = useState<"light" | "dark">("dark")
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (!mounted) return

    const savedTheme = localStorage.getItem("theme") as "light" | "dark" | null
    setTheme(savedTheme || "dark")
  }, [mounted])

  useEffect(() => {
    if (!mounted) return

    localStorage.setItem("theme", theme)
    if (theme === "dark") {
      document.documentElement.classList.add("dark")
    } else {
      document.documentElement.classList.remove("dark")
    }
  }, [theme, mounted])

  const toggleTheme = () => {
    setTheme(theme === "light" ? "dark" : "light")
  }

  return <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>{children}</ThemeContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeContextType {
  const context = useContext(ThemeContext)
  if (context === undefined) {
    throw new Error("useTheme must be used within a ThemeProvider")
  }
  return context
}