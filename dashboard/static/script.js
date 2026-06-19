document.addEventListener("DOMContentLoaded", () => {
    // 1. Core Config State Payload Bridge Unpacking Pipeline
    const stateEngineData = JSON.parse(document.getElementById("dashboard-state-payload-json").textContent);

    // 2. Multi-Module Navigation Tab Switch Engine (SPA View Layer Pipeline)
    const sidebarAnchors = document.querySelectorAll(".nav-anchor");
    const layoutViews = document.querySelectorAll(".spa-view-layer");

    sidebarAnchors.forEach(anchor => {
        anchor.addEventListener("click", (event) => {
            event.preventDefault();
            sidebarAnchors.forEach(el => el.classList.remove("active"));
            layoutViews.forEach(el => el.classList.add("view-hidden"));

            anchor.classList.add("active");
            const structuralTargetId = anchor.getAttribute("data-target");
            document.getElementById(structuralTargetId).classList.remove("view-hidden");
        });
    });

    // 3. Alerts Popover Floating Panel Component Overlay Toggles Handles
    const dropdownTrigger = document.getElementById("alert-dropdown-btn");
    const popoverOverlay = document.getElementById("alerts-popup-overlay");

    dropdownTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        popoverOverlay.classList.toggle("view-hidden");
    });

    document.addEventListener("click", (e) => {
        if (!popoverOverlay.classList.contains("view-hidden") && !popoverOverlay.contains(e.target)) {
            popoverOverlay.classList.add("view-hidden");
        }
    });

    // 4. Color Framework Application Core Theme Inversion Routine
    const themeControlBtn = document.getElementById("global-theme-toggle");
    const documentHtmlElement = document.documentElement;

    themeControlBtn.addEventListener("click", () => {
        const currentlyActiveTheme = documentHtmlElement.getAttribute("data-theme");
        const inverseCalculatedTheme = currentlyActiveTheme === "dark" ? "light" : "dark";
        documentHtmlElement.setAttribute("data-theme", inverseCalculatedTheme);
        
        // Re-compile Graphic Widget options colors parameters dynamically
        refreshDashboardCharts(inverseCalculatedTheme);
    });

    // 5. Global Chart.js Subsystem Processing Loops Configuration Matrix
    let runtimeChartHandles = {};

    function initializeAllDashboardWidgets(themeContext) {
        const isDarkThemeActive = themeContext === "dark";
        const gridBorderColor = isDarkThemeActive ? "#172033" : "#E2E8F0";
        const labelTextColor = isDarkThemeActive ? "#F4F5F7" : "#0F172A";

        // Setup 1: Home Dashboard Pie Configuration Widget
        const ctxHomePie = document.getElementById("homePieChart").getContext("2d");
        runtimeChartHandles.homePie = new Chart(ctxHomePie, {
            type: "doughnut",
            data: {
                labels: stateEngineData.charts.distribution.labels,
                datasets: [{
                    data: stateEngineData.charts.distribution.data,
                    backgroundColor: ["#3B82F6", "#F59E0B", "#EF4444"],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom", labels: { color: labelTextColor, font: { family: "Inter", size: 10 } } } }
            }
        });

        // Setup 2: Home Dashboard Market Conditions Strength Gauge Widget
        const ctxHomeGauge = document.getElementById("homeGaugeChart").getContext("2d");
        const innerStrengthDataValue = stateEngineData.market_state.market_strength;
        runtimeChartHandles.homeGauge = new Chart(ctxHomeGauge, {
            type: "doughnut",
            data: {
                datasets: [{
                    data: [innerStrengthDataValue, 100 - innerStrengthDataValue],
                    backgroundColor: ["#10B981", isDarkThemeActive ? "#172033" : "#E2E8F0"],
                    circumference: 180,
                    rotation: 270,
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "85%",
                plugins: { tooltip: { enabled: false }, legend: { display: false } }
            }
        });

        // Setup 3: Home Dashboard Historical Run Engine Breakouts Line Widget
        const ctxHomeLine = document.getElementById("homeLineChart").getContext("2d");
        runtimeChartHandles.homeLine = new Chart(ctxHomeLine, {
            type: "line",
            data: {
                labels: stateEngineData.charts.daily_signals.labels,
                datasets: [{
                    data: stateEngineData.charts.daily_signals.data,
                    borderColor: "#3B82F6",
                    backgroundColor: "rgba(59, 130, 246, 0.03)",
                    fill: true,
                    tension: 0.35,
                    borderWidth: 2,
                    pointRadius: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } },
                    y: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } }
                },
                plugins: { legend: { display: false } }
            }
        });

        // Setup 4: Portfolio Allocation Weight Matrix Pie Widget
        const ctxPortfolioPie = document.getElementById("portfolioPieChart").getContext("2d");
        runtimeChartHandles.portfolioPie = new Chart(ctxPortfolioPie, {
            type: "pie",
            data: {
                labels: stateEngineData.charts.asset_allocation.labels,
                datasets: [{
                    data: stateEngineData.charts.asset_allocation.data,
                    backgroundColor: ["#3B82F6", "#10B981", "#F59E0B", "#64748B"],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom", labels: { color: labelTextColor, font: { family: "Inter", size: 10 } } } }
            }
        });

        // Setup 5: Portfolio Compound Value Equity Growth Curve Line Widget
        const ctxPortfolioLine = document.getElementById("portfolioGrowthLineChart").getContext("2d");
        runtimeChartHandles.portfolioLine = new Chart(ctxPortfolioLine, {
            type: "line",
            data: {
                labels: stateEngineData.charts.portfolio_growth.labels,
                datasets: [{
                    data: stateEngineData.charts.portfolio_growth.data,
                    borderColor: "#10B981",
                    backgroundColor: "rgba(16, 185, 129, 0.03)",
                    fill: true,
                    tension: 0.2,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } },
                    y: { grid: { color: gridBorderColor }, ticks: { color: labelTextColor } }
                },
                plugins: { legend: { display: false } }
            }
        });
    }

    function refreshDashboardCharts(themeContext) {
        Object.keys(runtimeChartHandles).forEach(key => {
            if (runtimeChartHandles[key]) runtimeChartHandles[key].destroy();
        });
        initializeAllDashboardWidgets(themeContext);
    }

    // Default system initialization trace run (Dark Baseline)
    initializeAllDashboardWidgets("dark");
});
