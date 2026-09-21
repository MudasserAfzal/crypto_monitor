import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type CandlestickData, ColorType } from "lightweight-charts";
import { fetchOhlcv } from "../store";

interface Props {
  symbol: string;
}

export function PriceChart({ symbol }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8fa399",
      },
      grid: {
        vertLines: { color: "rgba(180,210,190,0.06)" },
        horzLines: { color: "rgba(180,210,190,0.06)" },
      },
      width: ref.current.clientWidth,
      height: 360,
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });
    const series = chart.addCandlestickSeries({
      upColor: "#3dd68c",
      downColor: "#f07167",
      borderVisible: false,
      wickUpColor: "#3dd68c",
      wickDownColor: "#f07167",
    });
    chartRef.current = chart;

    let alive = true;
    fetchOhlcv(symbol).then((rows) => {
      if (!alive) return;
      const data: CandlestickData[] = rows.map((r: any) => ({
        time: Math.floor(new Date(r.open_time).getTime() / 1000) as any,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
      }));
      series.setData(data);
      chart.timeScale().fitContent();
    }).catch(() => undefined);

    const onResize = () => {
      if (ref.current) chart.applyOptions({ width: ref.current.clientWidth });
    };
    window.addEventListener("resize", onResize);
    return () => {
      alive = false;
      window.removeEventListener("resize", onResize);
      chart.remove();
    };
  }, [symbol]);

  return <div className="chart-wrap" ref={ref} />;
}
