window.addEventListener("load", () => {
  gsap.registerPlugin(ScrollTrigger);
  let posX = 0, posY = 0;
  let mouseX = 0, mouseY = 0;

  const dot = document.getElementById("cursor-dot");
  const follower = document.getElementById("cursor-example");

  if (dot && follower) {
    gsap.to(dot, {
      duration: 0.01,
      repeat: -1,
      onRepeat: () => {
        gsap.set(dot, { css: { left: mouseX - 3, top: mouseY - 3 } });
      }
    });
    gsap.to(follower, {
      duration: 0.018,
      repeat: -1,
      onRepeat: () => {
        posX += (mouseX - posX) / 7;
        posY += (mouseY - posY) / 7;
        gsap.set(follower, { css: { left: posX - 12, top: posY - 12 } });
      }
    });
    document.addEventListener("mousemove", (e) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    });
    const hoverTargets = document.querySelectorAll("a, button");
    hoverTargets.forEach((target) => {
      target.addEventListener("mouseenter", () => {
        gsap.to(follower, { scale: 1.8, background: "#ffffff", opacity: 0.8, duration: 0.2 });
      });
      target.addEventListener("mouseleave", () => {
        gsap.to(follower, { scale: 1, background: "#22d3ee", opacity: 1, duration: 0.2 });
      });
    });
  }


  gsap.from(".slide-left", { opacity: 0, x: -50, duration: 0.9, ease: "power3.out" });
  gsap.from(".slide-right", { opacity: 0, x: 50, duration: 0.9, ease: "power3.out" });


  gsap.from(".framework-card", {
    scrollTrigger: {
      trigger: "#slide-2",
      start: "top 80%",   
      once: true          
    },
    opacity: 0,
    y: 40,
    stagger: 0.08,
    duration: 0.7,
    ease: "power2.out",
    clearProps: "all"     
  });
  gsap.from(".slide-trigger-3-left", {
    scrollTrigger: { trigger: "#slide-3", start: "top 80%", once: true },
    opacity: 0,
    x: -40,
    duration: 0.8,
    clearProps: "all"
  });

  gsap.from(".slide-trigger-3-right", {
    scrollTrigger: { trigger: "#slide-3", start: "top 80%", once: true },
    opacity: 0,
    x: 40,
    duration: 0.8,
    clearProps: "all"
  });
  gsap.from(".slide-4-img", {
    scrollTrigger: { trigger: "#slide-4", start: "top 80%", once: true },
    opacity: 0,
    scale: 0.96,
    duration: 0.8,
    clearProps: "all"
  });
  gsap.from(".slide-4-content > div", {
    scrollTrigger: { trigger: "#slide-4", start: "top 80%", once: true },
    opacity: 0,
    x: 30,
    stagger: 0.1,
    duration: 0.6,
    clearProps: "all"
  });
  gsap.from(".slide-5-left", {
    scrollTrigger: { trigger: "#slide-5", start: "top 80%", once: true },
    opacity: 0,
    y: 30,
    duration: 0.8,
    clearProps: "all"
  });
  gsap.from(".slide-5-right", {
    scrollTrigger: { trigger: "#slide-5", start: "top 80%", once: true },
    opacity: 0,
    scale: 0.96,
    duration: 0.8,
    clearProps: "all"
  });
  gsap.from(".slide-6-content h2", {
    scrollTrigger: { trigger: "#slide-6", start: "top 85%", once: true },
    opacity: 0,
    scale: 0.9,
    duration: 0.8,
    clearProps: "all"
  });
  ScrollTrigger.refresh();
});