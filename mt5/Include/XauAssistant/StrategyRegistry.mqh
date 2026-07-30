// StrategyRegistry.mqh — owns all compiled-in strategies; one is active.
// The active strategy trades and alerts; the rest are silent shadows.
#ifndef XAU_STRATEGYREGISTRY_MQH
#define XAU_STRATEGYREGISTRY_MQH
#include <XauAssistant/Strategy.mqh>

class CStrategyRegistry
  {
private:
   CStrategy *m_strategies[];
   int        m_active;

public:
   CStrategyRegistry() : m_active(-1) {}

   void Register(CStrategy *s)
     {
      int n = ArraySize(m_strategies);
      ArrayResize(m_strategies, n + 1);
      m_strategies[n] = s;
     }

   bool SetActive(string id)
     {
      for(int i = 0; i < ArraySize(m_strategies); i++)
         if(m_strategies[i].Id() == id) { m_active = i; return true; }
      return false;
     }

   CStrategy *Active()    { return (m_active >= 0) ? m_strategies[m_active] : NULL; }
   int        Count()     { return ArraySize(m_strategies); }
   CStrategy *Get(int i)  { return m_strategies[i]; }

   void Clear()
     {
      for(int i = 0; i < ArraySize(m_strategies); i++)
         if(CheckPointer(m_strategies[i]) == POINTER_DYNAMIC) delete m_strategies[i];
      ArrayResize(m_strategies, 0);
      m_active = -1;
     }
  };
#endif
